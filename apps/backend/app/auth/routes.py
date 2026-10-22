"""Auth routes: OIDC login/callback, local login, refresh-token rotation, logout, /api/me."""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_body import json_object
from app.auth.dependencies import get_authenticated_user, get_current_user
from app.auth.local_login import LocalLoginService
from app.auth.oidc import (
    OidcClient,
    code_challenge_from_verifier,
    generate_code_verifier,
    generate_nonce,
    generate_state,
)
from app.auth.password_change import change_own_password
from app.auth.refresh import RefreshService, TokenPair
from app.auth.revocation import revoke_access_token
from app.auth.schemas import CurrentUser
from app.auth.state_store import OidcStateStore
from app.auth.tenants import resolve_tenant_by_email_domain
from app.config import Settings
from app.core.exceptions import AuthError
from app.core.folding import fold
from app.db import get_session
from app.models.user import ACCOUNT_SOURCE_SSO, LOCAL_SUBJECT_PREFIX, User
from app.rate_limit import client_ip

router = APIRouter(tags=["auth"])

_REFRESH_COOKIE = "refresh_token"
# The refresh token is only ever sent to the refresh endpoint itself (C-3).
_REFRESH_COOKIE_PATH = "/api/auth/refresh"
# Wider path used before C-3 confined the cookie; browsers delete by name and path.
_PRE_CONFINEMENT_COOKIE_PATH = "/api/auth"
# Post-login landing page (served by the frontend, routed by nginx).
_FRONTEND_HOME = "/user"
# Login page, where a failed callback hands the browser back with its error code.
_FRONTEND_LOGIN = "/login"


def _login_error_redirect(code: str) -> RedirectResponse:
    """Answer a callback failure with C-24's exit: 302 to the login page.

    The error code travels as the ``error`` query parameter the login page reads;
    the response carries no cookie, so the failure neither issues nor renews a
    session. Clearing a live session here would let any caller with a bogus
    callback URL log an unrelated user out.
    """
    return RedirectResponse(f"{_FRONTEND_LOGIN}?{urlencode({'error': code})}", status_code=302)


def _retire_pre_confinement_cookie(response: Response) -> None:
    """Drop the wider-path duplicate a browser that logged in earlier still holds."""
    response.delete_cookie(_REFRESH_COOKIE, path=_PRE_CONFINEMENT_COOKIE_PATH)


def _set_refresh_cookie(response: Response, token_pair: TokenPair, settings: Settings) -> None:
    response.set_cookie(
        _REFRESH_COOKIE,
        token_pair.refresh_token,
        max_age=token_pair.refresh_expires_in,
        path=_REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    _retire_pre_confinement_cookie(response)


def _bearer_token(request: Request) -> str | None:
    """The access token of the request's ``Authorization: Bearer`` header, if any."""
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        return None
    return authorization[7:].strip() or None


async def _revoke_bearer_token(request: Request) -> None:
    """Mark the request's access token revoked, when it carries a bearer token.

    Logout still succeeds without one: the refresh family is revoked either way,
    and an access token the client kept to itself then only outlives its own
    short ``exp``.
    """
    token = _bearer_token(request)
    if token is None:
        return
    await revoke_access_token(getattr(request.app.state, "redis", None), token)


def _bearer_session_id(request: Request) -> uuid.UUID | None:
    """The session named by the request's access token, when that token is authentic.

    The signature is verified against the platform's public key; ``exp`` is
    deliberately not, because a browser reaches logout holding the token it last
    obtained, and C-3 keeps the refresh credential away from this endpoint —
    demanding an unexpired token would end no session at all.
    """
    token = _bearer_token(request)
    public_key = getattr(getattr(request.app.state, "settings", None), "jwt_public_key", "")
    if token is None or not public_key:
        return None
    try:
        claims = jwt.decode(token, public_key, algorithms=["RS256"], options={"verify_exp": False})
    except jwt.PyJWTError:
        return None
    raw = claims.get("sid")
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def _resolve_user(
    session: AsyncSession,
    *,
    claims: dict[str, object],
    userinfo: dict[str, object],
) -> User:
    """Match the IdP identity to a tenant (by email domain) and find/create the user.

    The lookups decide nothing on their own: the address and the subject are
    unique per tenant in the database (C-103), so a callback running at the same
    time can write between them. The constraint that then fires is answered as
    the same conflict the lookups raise, never as a server fault (C-24).
    """
    sub = claims.get("sub")
    email = userinfo.get("email") or claims.get("email")
    if not isinstance(sub, str) or not isinstance(email, str):
        raise AuthError(400, "INVALID_STATE", "IdP identity missing sub or email")
    if sub.startswith(LOCAL_SUBJECT_PREFIX):
        # The prefix is the local accounts' own namespace (C-114), so an IdP
        # subject carrying it names no identity here: it must not address a
        # local account through the lookup below, nor be created as an SSO one.
        raise AuthError(400, "INVALID_ID_TOKEN", "IdP subject is not usable")

    tenant = await resolve_tenant_by_email_domain(session, email)
    # Read the tenant's key now: the rollback below expires the ORM instance, so
    # the conflict branch must not reach back into it for one.
    tenant_id = tenant.id

    user = await session.scalar(
        select(User).where(
            User.tenant_id == tenant_id,
            User.sso_sub == sub,
            User.account_source == ACCOUNT_SOURCE_SSO,
        )
    )
    name = str(userinfo.get("name") or userinfo.get("preferred_username") or email)
    department = userinfo.get("department")

    if user is None:
        # The address identifies one account per tenant (C-103), and a source is
        # fixed at creation (C-114): an address already held by another account
        # — a local one in particular — is refused here, before anything is
        # written, and answered like the create path's conflict.
        held = await session.scalar(
            select(User.id)
            .where(
                User.tenant_id == tenant_id,
                User.email_folded == fold(email),
            )
            .limit(1)
        )
        if held is not None:
            raise AuthError(409, "ACCOUNT_EXISTS", "Account already exists")

        user = User(
            tenant_id=tenant_id,
            sso_sub=sub,
            email=email,
            name=name,
            department=department if isinstance(department, str) else None,
            role="employee",
            status="active",
            account_source=ACCOUNT_SOURCE_SSO,
        )
        session.add(user)
    else:
        user.email = email
        user.name = name
        if isinstance(department, str):
            user.department = department

    try:
        await session.flush()
        await session.commit()
    except IntegrityError:
        # Both writes can lose the race the lookups above cannot close, and the
        # email rewrite can land on an address another account already holds:
        # the constraint knows which, and its verdict is the conflict this
        # path answers (C-103 / C-23) — a redirect, not an error body (C-24).
        await session.rollback()
        stored = await session.scalar(
            select(User).where(
                User.tenant_id == tenant_id,
                User.sso_sub == sub,
                User.account_source == ACCOUNT_SOURCE_SSO,
            )
        )
        if stored is None or stored.email_folded != fold(email):
            raise AuthError(409, "ACCOUNT_EXISTS", "Account already exists") from None
        # A peer callback committed this very identity and address: the repeat
        # submission continues on the account that landed.
        user = stored
    return user


@router.get("/api/auth/login")
async def login(request: Request) -> RedirectResponse:
    """Start the OIDC authorization-code + PKCE flow (302 to the IdP)."""
    store: OidcStateStore = request.app.state.state_store
    oidc: OidcClient = request.app.state.oidc

    if not oidc.is_configured:
        raise AuthError(503, "OIDC_UNAVAILABLE", "OIDC is not configured")

    state = generate_state()
    nonce = generate_nonce()
    code_verifier = generate_code_verifier()
    stored = await store.put(state, {"code_verifier": code_verifier, "nonce": nonce})
    if not stored:
        raise AuthError(500, "STATE_STORE_ERROR", "Failed to store OIDC state")

    url = await oidc.build_authorization_url(
        state=state,
        nonce=nonce,
        code_challenge=code_challenge_from_verifier(code_verifier),
    )
    return RedirectResponse(url, status_code=302)


async def _establish_session(
    request: Request,
    session: AsyncSession,
    *,
    code: str | None,
    state: str | None,
) -> RedirectResponse:
    """Verify the callback's code and identity, then answer the session redirect.

    Every refusal raises :class:`AuthError`, which the route presents on the
    login page rather than as a JSON body (C-24).
    """
    settings: Settings = request.app.state.settings
    store: OidcStateStore = request.app.state.state_store
    oidc: OidcClient = request.app.state.oidc
    refresh_service: RefreshService = request.app.state.refresh_service

    if not code or not state:
        raise AuthError(400, "INVALID_STATE", "OIDC state is missing")

    stored = await store.consume(state)
    if stored is None:
        raise AuthError(400, "INVALID_STATE", "OIDC state is invalid or already used")

    tokens = await oidc.exchange_code(code, stored["code_verifier"])
    id_token = tokens.get("id_token")
    access_token = tokens.get("access_token")
    if not isinstance(id_token, str) or not isinstance(access_token, str):
        raise AuthError(400, "INVALID_STATE", "IdP token response missing id_token")

    jwks = await oidc.fetch_jwks()
    claims = oidc.verify_id_token(id_token, nonce=stored["nonce"], jwks=jwks)
    userinfo = await oidc.fetch_userinfo(access_token)

    user = await _resolve_user(session, claims=claims, userinfo=userinfo)
    token_pair = await refresh_service.issue(session, user)

    response = RedirectResponse(_FRONTEND_HOME, status_code=302)
    _set_refresh_cookie(response, token_pair, settings)
    return response


@router.get("/api/auth/callback")
async def callback(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    """Consume the OIDC callback: verify id_token, resolve user, set session.

    The browser arrives here as a redirect, so a refusal must not answer with the
    JSON error envelope: it goes back to the login page with its error code
    (C-24).
    """
    try:
        return await _establish_session(request, session, code=code, state=state)
    except AuthError as exc:
        return _login_error_redirect(exc.code)


@router.get("/api/auth/capabilities")
async def capabilities(request: Request) -> dict[str, bool]:
    """Public capability read point (C-110).

    Answers without authentication and reports the state in both directions: a
    deployment that never declared the local-login capability reads as disabled
    rather than as missing.
    """
    settings: Settings = request.app.state.settings
    return {"localLoginEnabled": settings.local_login_enabled}


@router.post("/api/auth/login/local")
async def local_login(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    """Authenticate a local account with an email and password (C-111).

    Body: ``{"email": "<address>", "password": "<secret>"}``. The body is parsed
    here rather than declared, because the capability switch and the login rate
    limit are judged before the request fields.

    Success answers 200 with whether the account must change its password and
    sets the refresh cookie (C-3); the access token still comes from the refresh
    channel alone, and neither a password nor a token appears in this body.
    """
    settings: Settings = request.app.state.settings
    service: LocalLoginService = request.app.state.local_login_service
    refresh_service: RefreshService = request.app.state.refresh_service

    payload = await json_object(request)
    result = await service.authenticate(
        session,
        email=payload.get("email"),
        password=payload.get("password"),
        client_ip=client_ip(request.scope, settings.trusted_proxy_count),
    )
    token_pair = await refresh_service.issue(session, result.user)
    _set_refresh_cookie(response, token_pair, settings)
    return {"mustChangePassword": result.must_change_password}


@router.post("/api/auth/password")
async def change_password(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_authenticated_user)],
) -> dict[str, str]:
    """Change the authenticated local account's password (C-113).

    This is the one business path a forced-change account may still use, so it
    takes the gate-free identity dependency. Body: ``{"currentPassword": …,
    "newPassword": …}`` — read here rather than declared, so a rejected body is
    reported by field name and never echoes a password. Success ends every
    refresh-token family of the account, so the next login uses the new
    password; access tokens already issued stay valid (C-29's 900 s window).
    """
    refresh_service: RefreshService = request.app.state.refresh_service
    body = await json_object(request)
    await change_own_password(session, user, body=body, refresh_service=refresh_service)
    return {"message": "密码已修改"}


@router.post("/api/auth/refresh")
async def refresh(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str | int]:
    """Rotate the refresh token from the HttpOnly cookie; issues a new pair.

    Contract: ``200 { accessToken, expiresIn: 900 }`` + a rotated refresh
    cookie. The access token is delivered here because the callback is a 302
    redirect with no body — this is the only channel through which the browser
    obtains a Bearer token to call ``/api/me`` and the protected APIs.
    """
    settings: Settings = request.app.state.settings
    refresh_service: RefreshService = request.app.state.refresh_service

    raw_token = request.cookies.get(_REFRESH_COOKIE)
    if not raw_token:
        raise AuthError(401, "UNAUTHORIZED", "Missing refresh token")

    token_pair = await refresh_service.rotate(session, raw_token)
    _set_refresh_cookie(response, token_pair, settings)
    return {"accessToken": token_pair.access_token, "expiresIn": token_pair.expires_in}


@router.post("/api/auth/logout")
async def logout(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """End the presented session: revoke its access token and refresh-token family."""
    refresh_service: RefreshService = request.app.state.refresh_service

    await _revoke_bearer_token(request)

    # C-3 keeps the browser from submitting the refresh credential here, so the
    # session is identified by the access token's ``sid``; a client without one
    # falls back to the credential it presented.
    session_id = _bearer_session_id(request)
    if session_id is not None:
        await refresh_service.revoke_family(session, session_id, reason="logout")
    else:
        raw_token = request.cookies.get(_REFRESH_COOKIE)
        if raw_token:
            await refresh_service.revoke(session, raw_token, reason="logout")
    response.delete_cookie(_REFRESH_COOKIE, path=_REFRESH_COOKIE_PATH)
    _retire_pre_confinement_cookie(response)
    return {"message": "已登出"}


@router.get("/api/me", response_model=CurrentUser)
async def me(user: Annotated[User, Depends(get_current_user)]) -> CurrentUser:
    """Return the authenticated user's profile."""
    return CurrentUser(
        id=user.id,
        tenantId=user.tenant_id,
        email=user.email,
        name=user.name,
        role=user.role,
        department=user.department,
        avatarText=user.avatar_text,
    )
