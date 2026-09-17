"""Auth routes: OIDC login/callback, refresh-token rotation, logout, /api/me."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.oidc import (
    OidcClient,
    code_challenge_from_verifier,
    generate_code_verifier,
    generate_nonce,
    generate_state,
)
from app.auth.refresh import RefreshService, TokenPair
from app.auth.schemas import CurrentUser
from app.auth.state_store import OidcStateStore
from app.config import Settings
from app.core.exceptions import AuthError
from app.db import get_session
from app.models.tenant import Tenant
from app.models.user import User

router = APIRouter(tags=["auth"])

_REFRESH_COOKIE = "refresh_token"
# The refresh token is only ever sent to the auth sub-path, never site-wide.
_REFRESH_COOKIE_PATH = "/api/auth"
# Post-login landing page (served by the frontend, routed by nginx).
_FRONTEND_HOME = "/user"


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


async def _resolve_user(
    session: AsyncSession,
    *,
    claims: dict[str, object],
    userinfo: dict[str, object],
) -> User:
    """Match the IdP identity to a tenant (by email domain) and find/create the user."""
    sub = claims.get("sub")
    email = userinfo.get("email") or claims.get("email")
    if not isinstance(sub, str) or not isinstance(email, str):
        raise AuthError(400, "INVALID_STATE", "IdP identity missing sub or email")

    domain = email.rsplit("@", 1)[-1].lower()
    tenants = (
        await session.execute(
            select(Tenant).where(func.lower(Tenant.sso_domain) == domain)
        )
    ).scalars().all()
    if not tenants:
        raise AuthError(403, "TENANT_NOT_FOUND", "No tenant matches this SSO domain")
    if len(tenants) > 1:
        # Case-folded collisions (e.g. "Acme.com" vs "acme.com") make the
        # mapping ambiguous; refuse rather than guess the user's tenant.
        raise AuthError(403, "TENANT_AMBIGUOUS", "Multiple tenants match this SSO domain")
    tenant = tenants[0]

    user = await session.scalar(
        select(User).where(User.tenant_id == tenant.id, User.sso_sub == sub)
    )
    name = str(userinfo.get("name") or userinfo.get("preferred_username") or email)
    department = userinfo.get("department")

    if user is None:
        user = User(
            tenant_id=tenant.id,
            sso_sub=sub,
            email=email,
            name=name,
            department=department if isinstance(department, str) else None,
            role="employee",
            status="active",
        )
        session.add(user)
        await session.flush()
    else:
        user.email = email
        user.name = name
        if isinstance(department, str):
            user.department = department
    await session.commit()
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
    stored = await store.put(
        state, {"code_verifier": code_verifier, "nonce": nonce}
    )
    if not stored:
        raise AuthError(500, "STATE_STORE_ERROR", "Failed to store OIDC state")

    url = await oidc.build_authorization_url(
        state=state,
        nonce=nonce,
        code_challenge=code_challenge_from_verifier(code_verifier),
    )
    return RedirectResponse(url, status_code=302)


@router.get("/api/auth/callback")
async def callback(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    code: str | None = None,
    state: str | None = None,
) -> RedirectResponse:
    """Consume the OIDC callback: verify id_token, resolve user, set session."""
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
    """Revoke the refresh-token family and clear the cookie."""
    refresh_service: RefreshService = request.app.state.refresh_service

    raw_token = request.cookies.get(_REFRESH_COOKIE)
    if raw_token:
        await refresh_service.revoke(session, raw_token, reason="logout")
    response.delete_cookie(_REFRESH_COOKIE, path=_REFRESH_COOKIE_PATH)
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
