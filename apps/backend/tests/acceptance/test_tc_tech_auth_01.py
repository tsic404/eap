"""Design acceptance case TC-tech-auth-01.

Login initiation, OIDC callback (success and failure presentation), refresh
token rotation with the replay grace window, logout revocation and the carriage
of the refresh credential.

The upstream identity provider is mocked at the HTTP boundary (one
``httpx.MockTransport`` serving discovery, JWKS, token and userinfo), while the
platform's own behaviour is observed only through its HTTP responses and
``Set-Cookie`` headers.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
import redis.asyncio as aioredis
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import delete, select

from app.auth.oidc import OidcClient
from app.auth.refresh import RefreshService
from app.auth.state_store import OidcStateStore
from app.config import Settings
from app.models.refresh_token import RefreshToken
from app.models.tenant import Tenant
from app.models.user import User

# The future import and the imports above must precede every other statement;
# the case identifier leads the module body from here on.
TC_ID = "TC-tech-auth-01"

_ISSUER = "https://idp.example.com"
_CLIENT_ID = "eap-acceptance-client"
_SSO_DOMAIN = "acme.com"
_LOGIN_PAGE = "/login"
_USER_HOME = "/user"
_REFRESH_COOKIE = "refresh_token"
_REFRESH_ENDPOINT = "/api/auth/refresh"
_LOGOUT_ENDPOINT = "/api/auth/logout"
# The rotation grace window: a replay inside it is a retried double-submit.
_GRACE_WINDOW_SECONDS = 30
# C-24: every callback-stage failure must be a 302 to the login page carrying
# the error code. At main@68ad4b5 they are answered as JSON error bodies, so
# each case asserting the contract's presentation is registered as a gap.
_CALLBACK_FAILURE_XFAIL = pytest.mark.xfail(
    reason="C-24 callback failure redirect presentation not implemented at main@68ad4b5",
    strict=False,
)


# ── the mocked identity provider ─────────────────────────────────────────────


def _b64url(number: int) -> str:
    length = (number.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(number.to_bytes(length, "big")).rstrip(b"=").decode("ascii")


def _s256(value: str) -> str:
    """The PKCE ``S256`` challenge derived from a code verifier."""
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _digest(raw: str) -> str:
    """The storage digest a refresh token is persisted under."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class _FakeIdP:
    """Minimal OIDC provider served through ``httpx.MockTransport``."""

    def __init__(self, private_key: Any, *, signing_key: Any = None) -> None:
        self._private_key = private_key
        # The key advertised in the JWKS is ``_private_key``; ``signing_key``
        # may differ so a bad signature can be staged deliberately.
        self.signing_key = signing_key or private_key
        self.kid = "acceptance-key"
        self.claims: dict[str, Any] = {}
        self.drop_claims: set[str] = set()
        self.nonce = "unset"
        self.code_verifier = None
        self.discovery_status = 200
        self.jwks_status = 200
        self.token_status = 200
        self.token_body = None
        self.userinfo_status = 200
        self.userinfo = {
            "sub": "idp-user-1",
            "email": f"alice@{_SSO_DOMAIN}",
            "name": "Alice",
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            if self.discovery_status != 200:
                return httpx.Response(self.discovery_status, json={"error": "unavailable"})
            return httpx.Response(
                200,
                json={
                    "issuer": _ISSUER,
                    "authorization_endpoint": f"{_ISSUER}/authorize",
                    "token_endpoint": f"{_ISSUER}/token",
                    "userinfo_endpoint": f"{_ISSUER}/userinfo",
                    "jwks_uri": f"{_ISSUER}/jwks",
                },
            )
        if path == "/jwks":
            if self.jwks_status != 200:
                return httpx.Response(self.jwks_status, json={"error": "unavailable"})
            return httpx.Response(200, json=self._jwks())
        if path == "/token":
            form = urllib.parse.parse_qs(request.content.decode("utf-8"))
            self.code_verifier = form.get("code_verifier", [None])[0]
            if self.token_status != 200 or self.token_body is not None:
                return httpx.Response(self.token_status, json=self.token_body or {})
            return httpx.Response(
                200,
                json={
                    "id_token": self.id_token(),
                    "access_token": "idp-access-token",
                    "token_type": "Bearer",
                },
            )
        if path == "/userinfo":
            if self.userinfo_status != 200:
                return httpx.Response(self.userinfo_status, json={"error": "userinfo_failed"})
            return httpx.Response(200, json=self.userinfo)
        return httpx.Response(404, json={"error": "not_found"})

    def _jwks(self) -> dict[str, Any]:
        numbers = self._private_key.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "alg": "RS256",
                    "use": "sig",
                    "kid": "acceptance-key",
                    "n": _b64url(numbers.n),
                    "e": _b64url(numbers.e),
                }
            ]
        }

    def id_token(self) -> str:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "iss": _ISSUER,
            "aud": _CLIENT_ID,
            "sub": self.userinfo.get("sub"),
            "nonce": self.nonce,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        }
        # Claim overrides and omissions stage the id_token failure modes.
        payload.update(self.claims)
        for claim in self.drop_claims:
            payload.pop(claim, None)
        return jwt.encode(payload, self.signing_key, algorithm="RS256", headers={"kid": self.kid})


class _UnwritableStateStore:
    """A state store whose write fails, staging C-22's ``STATE_STORE_ERROR``."""

    async def put(self, state: str, payload: dict[str, Any]) -> bool:
        del state, payload
        return False

    async def consume(self, state: str) -> dict[str, Any] | None:
        del state
        return None


# ── wiring the auth runtime on the shared app ───────────────────────────────


def _auth_settings(api: Any, *, cookie_secure: bool = False, configured: bool = True) -> Settings:
    """Copy the test settings, adding the OIDC and access-token material."""
    private_pem = api.private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    issuer = _ISSUER if configured else ""
    client_id = _CLIENT_ID if configured else ""
    # ``model_copy`` keeps the running test's database/redis/key settings and
    # only overrides the fields the auth flow reads.
    return api.settings.model_copy(
        update={
            "oidc_issuer": issuer,
            "oidc_client_id": client_id,
            "jwt_private_key": private_pem,
            "cookie_secure": cookie_secure,
        }
    )


@asynccontextmanager
async def _auth_stack(
    api: Any, idp: _FakeIdP, *, cookie_secure: bool = False, configured: bool = True
) -> AsyncIterator[None]:
    """Attach the auth runtime (OIDC client, state store, rotation, registry)."""
    settings = _auth_settings(api, cookie_secure=cookie_secure, configured=configured)
    redis = aioredis.from_url(api.settings.redis_url)
    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(idp))
    api.app.state.settings = settings
    api.app.state.redis = redis
    api.app.state.oidc = OidcClient(settings, client=mock_http)
    api.app.state.state_store = OidcStateStore(redis)
    api.app.state.refresh_service = RefreshService(settings)
    try:
        yield
    finally:
        await mock_http.aclose()
        await redis.aclose()


# ── seeding and flow helpers ────────────────────────────────────────────────


async def _seed_sso_tenant(
    api: Any, *, sso_domain: str | None = _SSO_DOMAIN, status: str = "active"
) -> Tenant:
    """Seed a tenant whose SSO domain matches the mocked provider's e-mail."""
    async with api.db.session() as session:
        tenant = Tenant(
            name="Acme",
            slug=f"acme-{uuid.uuid4().hex[:8]}",
            sso_domain=sso_domain,
            sso_provider="oidc",
            status=status,
        )
        session.add(tenant)
        await session.commit()
        return tenant


async def _seed_refresh_token(
    api: Any,
    user: Any,
    *,
    family_id: uuid.UUID | None = None,
    revoked_at: datetime | None = None,
    revoked_reason: str | None = None,
    expires_in_seconds: int = 3600,
) -> str:
    """Insert one refresh-token row and return the raw value the client holds."""
    raw = secrets.token_urlsafe(48)
    async with api.db.session() as session:
        session.add(
            RefreshToken(
                user_id=user.id,
                family_id=family_id or uuid.uuid4(),
                token_hash=_digest(raw),
                expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
                revoked_at=revoked_at,
                revoked_reason=revoked_reason,
            )
        )
        await session.commit()
    return raw


def _query(location: str) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(location).query)


def _cookie_header(raw: str) -> dict[str, str]:
    """The request header that presents one refresh credential explicitly."""
    return {"Cookie": f"{_REFRESH_COOKIE}={raw}"}


async def _backdate_rotation(api: Any, raw: str) -> None:
    """Move a real rotation's revocation beyond the 30-second grace window."""
    async with api.db.session() as session:
        token = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == _digest(raw))
        )
        assert token is not None, "the token must exist to be backdated"
        token.revoked_at = datetime.now(UTC) - timedelta(seconds=_GRACE_WINDOW_SECONDS + 5)
        await session.commit()


async def _begin_login(api: Any) -> tuple[str, str, str]:
    """GET the login endpoint and return ``(state, nonce, code_challenge)``."""
    response = await api.client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 302, response.text
    query = _query(response.headers["location"])
    return query["state"][0], query["nonce"][0], query["code_challenge"][0]


async def _complete_callback(api: Any, idp: _FakeIdP, *, state: str, nonce: str) -> httpx.Response:
    """Answer the callback with the nonce the provider will sign into the id_token."""
    idp.nonce = nonce
    return await api.client.get(
        f"/api/auth/callback?code=authorization-code&state={state}", follow_redirects=False
    )


async def _login(api: Any, idp: _FakeIdP) -> httpx.Response:
    """Drive login → callback and return the callback response."""
    state, nonce, _ = await _begin_login(api)
    return await _complete_callback(api, idp, state=state, nonce=nonce)


def _parse_cookie(header: str) -> dict[str, str]:
    """Parse one ``Set-Cookie`` header into a lowercase attribute map."""
    segments = [segment.strip() for segment in header.split(";")]
    name, _, value = segments[0].partition("=")
    attrs = {"name": name, "value": value}
    for segment in segments[1:]:
        key, sep, val = segment.partition("=")
        attrs[key.lower()] = val if sep else "true"
    return attrs


def _cookie_attrs(response: httpx.Response) -> dict[str, str]:
    """Parse the response's first ``Set-Cookie`` header."""
    headers = response.headers.get_list("set-cookie")
    assert headers, "expected a Set-Cookie header"
    return _parse_cookie(headers[0])


def _cookies(response: httpx.Response) -> list[dict[str, str]]:
    """Every cookie the response sets, attributes parsed."""
    return [_parse_cookie(header) for header in response.headers.get_list("set-cookie")]


def _assert_no_token_cookie(response: httpx.Response) -> None:
    """C-3/F4: the access token never reaches the browser as a cookie.

    Any cookie that actually carries a value must be HttpOnly, so page scripts
    cannot read it either.
    """
    for cookie in _cookies(response):
        name = cookie["name"].lower()
        assert "access" not in name, f"access-token cookie issued: {cookie['name']}"
        assert "jwt" not in name, f"token cookie issued: {cookie['name']}"
        if cookie["value"].strip('"'):
            assert cookie["httponly"] == "true", f"script-readable cookie: {cookie['name']}"


def _assert_no_session_cookie(response: httpx.Response) -> None:
    """C-24: the failure neither issues nor retains a session credential.

    Clearing an existing cookie (empty value, ``Max-Age=0``) stays allowed.
    """
    for cookie in _cookies(response):
        assert cookie["value"].strip('"') == "", f"session cookie issued: {cookie['name']}"


def _assert_login_error_redirect(response: httpx.Response, *codes: str) -> None:
    """A callback failure lands on the login page with its code and no session."""
    assert response.status_code == 302, response.text
    target = urllib.parse.urlparse(response.headers.get("location", ""))
    assert target.path == _LOGIN_PAGE
    errors = _query(response.headers["location"]).get("error", [])
    assert errors and errors[0] in codes, f"expected one of {codes}, got {errors}"
    _assert_no_session_cookie(response)


def _access_token(response: httpx.Response) -> str:
    assert response.status_code == 200, response.text
    token = response.json()["data"]["accessToken"]
    assert token
    return token


# ── C-22: login initiation ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_redirects_to_the_provider_with_authorization_code_and_pkce_params(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        response = await api.client.get(
            "/api/auth/login", follow_redirects=False, params={"provider": "oidc"}
        )
        assert response.status_code == 302, response.text
        location = response.headers["location"]
        target = urllib.parse.urlparse(location)
        assert f"{target.scheme}://{target.netloc}{target.path}" == f"{_ISSUER}/authorize"
        query = _query(location)
        state = query["state"][0]
        nonce = query["nonce"][0]
        assert query["response_type"] == ["code"]
        assert query["client_id"] == [_CLIENT_ID]
        assert query["redirect_uri"] == [api.app.state.settings.oidc_redirect_uri]
        # C-22 fixes the identity scope, not one literal value for it.
        scopes = set(query["scope"][0].split())
        assert scopes and "openid" in scopes
        assert query["code_challenge_method"] == ["S256"]
        assert len(state) >= 20 and len(nonce) >= 20
        assert state != nonce

        # PKCE is only really bound if the challenge is the S256 digest of the
        # verifier the provider receives at the token exchange.
        callback = await _complete_callback(api, idp, state=state, nonce=nonce)
        assert callback.status_code == 302, callback.text
        assert idp.code_verifier
        assert query["code_challenge"] == [_s256(idp.code_verifier)]


@pytest.mark.asyncio
async def test_login_ignores_unknown_query_parameters(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        response = await api.client.get(
            "/api/auth/login", follow_redirects=False, params={"next": "/admin", "foo": "bar"}
        )
        assert response.status_code == 302, response.text
        query = _query(response.headers["location"])
        assert query["code_challenge_method"] == ["S256"]
        assert query["state"] and query["nonce"]
        # Ignoring an unknown parameter means it is not forwarded either.
        assert "next" not in query and "foo" not in query


@pytest.mark.asyncio
async def test_login_without_a_configured_provider_is_unavailable(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp, configured=False):
        response = await api.client.get("/api/auth/login", follow_redirects=False)
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "OIDC_UNAVAILABLE"


@pytest.mark.asyncio
async def test_login_when_provider_discovery_fails_is_unavailable(api) -> None:
    idp = _FakeIdP(api.private_key)
    idp.discovery_status = 500
    async with _auth_stack(api, idp):
        response = await api.client.get("/api/auth/login", follow_redirects=False)
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "OIDC_UNAVAILABLE"


@pytest.mark.asyncio
async def test_login_when_the_state_store_write_fails_is_a_server_error(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        api.app.state.state_store = _UnwritableStateStore()

        # C-22: the verification material is part of the login; failing to
        # persist it is a server-side fault, not an unavailable provider.
        response = await api.client.get("/api/auth/login", follow_redirects=False)
        assert response.status_code == 500, response.text
        assert response.json()["error"]["code"] == "STATE_STORE_ERROR"


# ── C-3 / F4: the browser's only token channel ──────────────────────────────


@pytest.mark.asyncio
async def test_no_response_hands_the_browser_a_token_outside_the_refresh_body(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        start = await api.client.get("/api/auth/login", follow_redirects=False)
        callback = await _login(api, idp)
        refresh = await api.client.post(_REFRESH_ENDPOINT)

        assert start.status_code == 302, start.text
        assert callback.status_code == 302, callback.text
        assert callback.headers["location"] == _USER_HOME
        assert refresh.status_code == 200, refresh.text

        for response in (start, callback, refresh):
            _assert_no_token_cookie(response)
        # C-3: the access token is handed over in the refresh response body and
        # nowhere else — neither redirect may carry it inline.
        assert "eyJ" not in start.text
        assert "eyJ" not in callback.text
        assert "idp-access-token" not in callback.text
        assert refresh.json()["data"]["accessToken"].startswith("eyJ")


# ── C-23 / C-24: callback failures ──────────────────────────────────────────


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_with_unknown_state_redirects_to_login_with_state_error(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        response = await api.client.get(
            "/api/auth/callback?code=authorization-code&state=unknown-state",
            follow_redirects=False,
        )
        _assert_login_error_redirect(response, "INVALID_STATE")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_with_consumed_state_redirects_to_login_with_state_error(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        first = await _complete_callback(api, idp, state=state, nonce=nonce)
        # The replay is only evidence of one-shot consumption if the first
        # callback actually established a session: a failed callback also
        # consumes the state, and C-24 presents both as a 302 to /login.
        assert first.status_code == 302, first.text
        assert first.headers["location"] == _USER_HOME
        assert _cookie_attrs(first)["name"] == _REFRESH_COOKIE

        replay = await api.client.get(
            f"/api/auth/callback?code=authorization-code&state={state}", follow_redirects=False
        )
        _assert_login_error_redirect(replay, "INVALID_STATE")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_with_missing_state_redirects_to_login_with_state_error(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        response = await api.client.get(
            "/api/auth/callback?code=authorization-code", follow_redirects=False
        )
        _assert_login_error_redirect(response, "INVALID_STATE")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_token_exchange_failure_redirects_with_exchange_error(api) -> None:
    idp = _FakeIdP(api.private_key)
    idp.token_status = 400
    idp.token_body = {"error": "invalid_grant"}
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "OIDC_TOKEN_EXCHANGE_FAILED")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_when_nonce_does_not_match_redirects_to_login_with_state_error(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, _, _ = await _begin_login(api)
        response = await _complete_callback(
            api, idp, state=state, nonce="a-nonce-the-provider-never-received"
        )
        _assert_login_error_redirect(response, "INVALID_STATE")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_when_userinfo_fails_redirects_to_login_with_userinfo_error(api) -> None:
    idp = _FakeIdP(api.private_key)
    idp.userinfo_status = 400
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "OIDC_USERINFO_FAILED")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_without_a_matching_tenant_redirects_with_tenant_not_found(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api, sso_domain="other.example.com")
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "TENANT_NOT_FOUND")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_with_an_ambiguous_tenant_redirects_with_tenant_ambiguous(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        # C-23 resolves the tenant by case-folded domain match and refuses two
        # matching rows as TENANT_AMBIGUOUS. Note the tension this construction
        # lives on: C-103 lists the SSO domain as *globally unique*, so under a
        # case-folded uniqueness rule two domains differing only in case could
        # not coexist and the ambiguity branch would be unreachable; main@68ad4b5
        # enforces uniqueness case-sensitively. This case therefore pins the
        # C-23 answer for the state it can build, and the uniqueness scope is
        # left to design clarification (a proposed ADR), not decided here.
        await _seed_sso_tenant(api, sso_domain="acme.com")
        await _seed_sso_tenant(api, sso_domain="Acme.com")
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "TENANT_AMBIGUOUS")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_when_the_token_response_lacks_the_id_token_is_a_state_error(api) -> None:
    idp = _FakeIdP(api.private_key)
    idp.token_body = {"access_token": "idp-access-token", "token_type": "Bearer"}
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "INVALID_STATE")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_when_the_token_response_lacks_the_access_token_is_a_state_error(
    api,
) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        # The id_token is minted here, so the response carries one and only the
        # access token is missing.
        idp.nonce = nonce
        idp.token_body = {"id_token": idp.id_token(), "token_type": "Bearer"}
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "INVALID_STATE")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_when_the_signing_keys_are_unreachable_is_unavailable(api) -> None:
    idp = _FakeIdP(api.private_key)
    idp.jwks_status = 500
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "OIDC_UNAVAILABLE")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_with_an_unknown_signing_key_id_is_an_invalid_id_token(api) -> None:
    idp = _FakeIdP(api.private_key)
    idp.kid = "a-key-the-provider-never-published"
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "INVALID_ID_TOKEN")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_with_a_forged_signature_is_an_invalid_id_token(api) -> None:
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    idp = _FakeIdP(api.private_key, signing_key=forged)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "INVALID_ID_TOKEN")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_with_a_foreign_audience_is_an_invalid_id_token(api) -> None:
    idp = _FakeIdP(api.private_key)
    idp.claims = {"aud": "some-other-client"}
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "INVALID_ID_TOKEN")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_with_a_foreign_issuer_is_an_invalid_id_token(api) -> None:
    idp = _FakeIdP(api.private_key)
    idp.claims = {"iss": "https://other-idp.example.com"}
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "INVALID_ID_TOKEN")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_with_an_expired_id_token_is_an_invalid_id_token(api) -> None:
    idp = _FakeIdP(api.private_key)
    # Well past the 30-second clock-skew allowance C-23 grants.
    idp.claims = {"exp": datetime.now(UTC) - timedelta(minutes=5)}
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "INVALID_ID_TOKEN")


@pytest.mark.asyncio
async def test_callback_accepts_an_id_token_within_the_thirty_second_skew(api) -> None:
    idp = _FakeIdP(api.private_key)
    # Ten seconds past ``exp``: inside the allowance, so the login must stand.
    idp.claims = {"exp": datetime.now(UTC) - timedelta(seconds=10)}
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        callback = await _login(api, idp)

        assert callback.status_code == 302, callback.text
        assert callback.headers["location"] == _USER_HOME
        assert _cookie_attrs(callback)["name"] == _REFRESH_COOKIE


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_without_an_email_is_a_state_error(api) -> None:
    idp = _FakeIdP(api.private_key)
    idp.userinfo = {"sub": "idp-user-1", "name": "Alice"}
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)
        _assert_login_error_redirect(response, "INVALID_STATE")


@pytest.mark.asyncio
@_CALLBACK_FAILURE_XFAIL
async def test_callback_without_a_subject_is_rejected(api) -> None:
    idp = _FakeIdP(api.private_key)
    idp.drop_claims = {"sub"}
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        state, nonce, _ = await _begin_login(api)
        response = await _complete_callback(api, idp, state=state, nonce=nonce)

        # C-23 refuses an identity with no subject; whether the claim-existence
        # check (INVALID_ID_TOKEN) or the provisioning check (INVALID_STATE)
        # fires first, the answer is a 400 code and never an account.
        _assert_login_error_redirect(response, "INVALID_ID_TOKEN", "INVALID_STATE")


# ── C-23: successful callback and account provisioning ──────────────────────


@pytest.mark.asyncio
async def test_callback_provisions_an_active_employee_and_lands_on_the_user_home(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        tenant = await _seed_sso_tenant(api)

        callback = await _login(api, idp)
        assert callback.status_code == 302, callback.text
        assert callback.headers["location"] == _USER_HOME
        cookie = _cookie_attrs(callback)
        assert cookie["name"] == _REFRESH_COOKIE
        assert cookie["value"]

        # Reading the profile proves the account exists and is active: the
        # gate rejects a non-active user before the handler runs.
        refresh = await api.client.post(_REFRESH_ENDPOINT)
        profile = await api.client.get(
            "/api/me", headers={"Authorization": f"Bearer {_access_token(refresh)}"}
        )
        assert profile.status_code == 200, profile.text
        body = profile.json()["data"]
        assert body["email"] == idp.userinfo["email"]
        assert body["role"] == "employee"
        assert body["tenantId"] == str(tenant.id)


@pytest.mark.asyncio
async def test_relogin_syncs_the_stored_profile_from_the_provider(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        first = await _login(api, idp)
        assert first.status_code == 302, first.text

        idp.userinfo["name"] = "Alice Renamed"
        idp.userinfo["department"] = "Platform"
        idp.userinfo["email"] = f"alice.renamed@{_SSO_DOMAIN}"
        second = await _login(api, idp)
        assert second.status_code == 302, second.text

        refresh = await api.client.post(_REFRESH_ENDPOINT)
        profile = await api.client.get(
            "/api/me", headers={"Authorization": f"Bearer {_access_token(refresh)}"}
        )
        body = profile.json()["data"]
        assert body["name"] == "Alice Renamed"
        assert body["department"] == "Platform"
        assert body["email"] == f"alice.renamed@{_SSO_DOMAIN}"


# ── C-25: refresh rotation and replay handling ──────────────────────────────


@pytest.mark.asyncio
async def test_refresh_returns_a_new_access_token_and_a_rotated_cookie(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        await _login(api, idp)
        original = api.client.cookies.get(_REFRESH_COOKIE)
        assert original

        response = await api.client.post(_REFRESH_ENDPOINT)
        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["expiresIn"] == 900
        assert body["accessToken"]
        rotated = _cookie_attrs(response)
        assert rotated["name"] == _REFRESH_COOKIE
        assert rotated["value"] and rotated["value"] != original

        profile = await api.client.get(
            "/api/me", headers={"Authorization": f"Bearer {body['accessToken']}"}
        )
        assert profile.status_code == 200, profile.text

        # C-25: rotation is mandatory — the superseded token is no longer a
        # credential. Presented right after the rotation it falls inside the
        # 30-second window and must read as a retried double-submit.
        replay = await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(original))
        assert replay.status_code == 409, replay.text
        assert replay.json()["error"]["code"] == "TOKEN_REFRESH_IN_PROGRESS"


@pytest.mark.asyncio
async def test_refresh_without_a_cookie_is_unauthorized(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        response = await api.client.post(_REFRESH_ENDPOINT)
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_refresh_with_an_unknown_token_is_expired(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        unknown = secrets.token_urlsafe(48)
        response = await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(unknown))
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_refresh_with_an_expired_token_is_expired(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        _, user = await api.seed(role="employee")
        expired = await _seed_refresh_token(api, user, expires_in_seconds=-60)
        response = await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(expired))
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_refresh_replay_inside_the_grace_window_is_in_progress(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        _, user = await api.seed(role="employee")
        original = await _seed_refresh_token(api, user)

        rotated = await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(original))
        assert rotated.status_code == 200, rotated.text
        successor = _cookie_attrs(rotated)["value"]
        assert successor != original

        # A double-submit of the superseded token inside the window is a retry…
        replay = await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(original))
        assert replay.status_code == 409, replay.text
        assert replay.json()["error"]["code"] == "TOKEN_REFRESH_IN_PROGRESS"
        # …and C-25 keeps the session: the successor still refreshes.
        after = await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(successor))
        assert after.status_code == 200, after.text
        assert after.json()["data"]["accessToken"]


@pytest.mark.asyncio
async def test_refresh_replay_after_the_grace_window_revokes_the_whole_family(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        _, user = await api.seed(role="employee")
        original = await _seed_refresh_token(api, user)

        rotated = await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(original))
        assert rotated.status_code == 200, rotated.text
        successor = _cookie_attrs(rotated)["value"]

        # The only way past the window without waiting half a minute is to age
        # the rotation the platform just recorded.
        await _backdate_rotation(api, original)

        replay = await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(original))
        assert replay.status_code == 401, replay.text
        assert replay.json()["error"]["code"] == "TOKEN_REUSE_DETECTED"

        # The breach revokes the family, so the genuine successor dies with it.
        after = await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(successor))
        assert after.status_code == 401, after.text
        assert after.json()["error"]["code"] == "TOKEN_REUSE_DETECTED"


@pytest.mark.asyncio
async def test_refresh_replay_of_a_logout_revoked_token_is_reuse_detected(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        _, user = await api.seed(role="employee")
        revoked = await _seed_refresh_token(
            api,
            user,
            revoked_at=datetime.now(UTC) - timedelta(seconds=2),
            revoked_reason="logout",
        )
        response = await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(revoked))
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "TOKEN_REUSE_DETECTED"


# ── C-25: an owner that no longer exists ────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_after_the_owner_is_deleted_is_expired(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        _, user = await api.seed(role="employee")
        orphaned = await _seed_refresh_token(api, user)

        # C-25: a credential whose owner no longer exists is dead. Deleting the
        # owner removes the whole family with it (C-105 cascades), so the
        # presented cookie must report the same expired answer as a token the
        # platform never issued — never a fresh session.
        async with api.db.session() as session:
            await session.execute(delete(User).where(User.id == user.id))
            await session.commit()

        response = await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(orphaned))
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


# ── C-26 / C-27: logout ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logout_without_credentials_succeeds_and_is_idempotent(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        first = await api.client.post(_LOGOUT_ENDPOINT)
        assert first.status_code == 200, first.text
        cleared = _cookie_attrs(first)
        assert cleared["name"] == _REFRESH_COOKIE
        assert cleared["value"].strip('"') == ""
        assert cleared["max-age"] == "0"

        second = await api.client.post(_LOGOUT_ENDPOINT)
        assert second.status_code == 200, second.text


@pytest.mark.asyncio
async def test_logout_clears_the_cookie_and_revokes_the_refresh_family(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        await _login(api, idp)
        active = api.client.cookies.get(_REFRESH_COOKIE)
        assert active

        logout = await api.client.post(_LOGOUT_ENDPOINT)
        assert logout.status_code == 200, logout.text
        assert _cookie_attrs(logout)["max-age"] == "0"

        replay = await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(active))
        assert replay.status_code == 401, replay.text
        # C-25: only a rotation replay inside the window is retryable (409);
        # a token revoked by logout is a breach, whatever the window says.
        assert replay.json()["error"]["code"] == "TOKEN_REUSE_DETECTED"


@pytest.mark.asyncio
async def test_logout_makes_the_presented_access_token_unauthorized(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        await _login(api, idp)
        access_token = _access_token(await api.client.post(_REFRESH_ENDPOINT))
        headers = {"Authorization": f"Bearer {access_token}"}
        assert (await api.client.get("/api/me", headers=headers)).status_code == 200

        logout = await api.client.post(_LOGOUT_ENDPOINT, headers=headers)
        assert logout.status_code == 200, logout.text

        revoked = await api.client.get("/api/me", headers=headers)
        assert revoked.status_code == 401, revoked.text
        assert revoked.json()["error"]["code"] == "TOKEN_REVOKED"


# ── C-3: refresh-credential carriage ────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_cookie_is_httponly_lax_and_without_a_domain(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        issued = _cookie_attrs(await _login(api, idp))
        rotated = _cookie_attrs(await api.client.post(_REFRESH_ENDPOINT))

        for cookie in (issued, rotated):
            assert cookie["name"] == _REFRESH_COOKIE
            assert cookie["httponly"] == "true"
            assert cookie["samesite"] == "lax"
            # No wide-scope attribute: the credential is not sent site-wide.
            # The exact path confinement C-3 requires is pinned by
            # ``test_refresh_cookie_path_is_confined_to_the_refresh_endpoint``.
            assert cookie["path"] != "/"
            assert "domain" not in cookie
            assert cookie["max-age"] == str(api.settings.refresh_token_ttl_seconds)


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason=(
        "C-3 refresh credential path confined to the refresh endpoint "
        "not implemented at main@68ad4b5"
    ),
    strict=False,
)
async def test_refresh_cookie_path_is_confined_to_the_refresh_endpoint(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp):
        await _seed_sso_tenant(api)
        issued = _cookie_attrs(await _login(api, idp))
        rotated = _cookie_attrs(await api.client.post(_REFRESH_ENDPOINT))

        # C-3: the path is limited to the refresh endpoint itself, so the
        # browser never presents this credential to login, callback or logout.
        assert issued["path"] == _REFRESH_ENDPOINT
        assert rotated["path"] == _REFRESH_ENDPOINT


@pytest.mark.asyncio
async def test_refresh_cookie_is_secure_when_the_deployment_uses_https(api) -> None:
    idp = _FakeIdP(api.private_key)
    async with _auth_stack(api, idp, cookie_secure=True):
        await _seed_sso_tenant(api)
        issued = _cookie_attrs(await _login(api, idp))
        assert issued["secure"] == "true"

        # A secure cookie is not stored by a client speaking plain HTTP, so the
        # rotation is driven with the credential the browser would send.
        rotated = _cookie_attrs(
            await api.client.post(_REFRESH_ENDPOINT, headers=_cookie_header(issued["value"]))
        )
        assert rotated["secure"] == "true"
        assert rotated["httponly"] == "true"
