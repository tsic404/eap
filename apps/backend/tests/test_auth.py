"""OIDC client and JWT issuance unit tests, plus auth-route and callback-chain tests."""

from __future__ import annotations

import base64
import hashlib
import urllib.parse
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.auth.oidc import OidcClient, code_challenge_from_verifier, generate_code_verifier
from app.auth.refresh import RefreshService
from app.auth.state_store import OidcStateStore
from app.auth.tokens import issue_access_token
from app.config import Settings
from app.core.exceptions import AuthError
from app.db import get_session
from app.main import create_app
from app.models.tenant import Tenant
from tests.conftest import FakeRedis

_ISSUER = "https://sso.example.com"
_CLIENT_ID = "client-123"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_PEM = (
    _KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)
_PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()


def _int_to_b64url(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()


def _jwks() -> dict[str, object]:
    numbers = _KEY.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "kid": "test-key",
                "n": _int_to_b64url(numbers.n),
                "e": _int_to_b64url(numbers.e),
            }
        ]
    }


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        oidc_issuer=_ISSUER,
        oidc_client_id=_CLIENT_ID,
        jwt_private_key=_PRIVATE_PEM,
        jwt_public_key=_PUBLIC_PEM,
    )


def _id_token(*, nonce: str, **overrides: object) -> str:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": "user-1",
        "nonce": nonce,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        **overrides,
    }
    return jwt.encode(payload, _KEY, algorithm="RS256", headers={"kid": "test-key"})


def test_code_challenge_is_s256() -> None:
    verifier = generate_code_verifier()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert code_challenge_from_verifier(verifier) == expected
    assert len(verifier) >= 43


def test_verify_id_token_accepts_valid() -> None:
    client = OidcClient(_settings())
    claims = client.verify_id_token(_id_token(nonce="n1"), nonce="n1", jwks=_jwks())
    assert claims["sub"] == "user-1"


def test_verify_id_token_rejects_nonce_mismatch() -> None:
    client = OidcClient(_settings())
    with pytest.raises(AuthError) as exc:
        client.verify_id_token(_id_token(nonce="n1"), nonce="n2", jwks=_jwks())
    assert exc.value.status_code == 400
    assert exc.value.code == "INVALID_STATE"


def test_verify_id_token_rejects_wrong_issuer() -> None:
    client = OidcClient(_settings())
    forged = _id_token(nonce="n1", iss="https://evil.example.com")
    with pytest.raises(AuthError) as exc:
        client.verify_id_token(forged, nonce="n1", jwks=_jwks())
    assert exc.value.code == "INVALID_ID_TOKEN"


def test_verify_id_token_rejects_malformed() -> None:
    client = OidcClient(_settings())
    with pytest.raises(AuthError) as exc:
        client.verify_id_token("not-a-jwt", nonce="n1", jwks=_jwks())
    assert exc.value.status_code == 400
    assert exc.value.code == "INVALID_ID_TOKEN"


def test_verify_id_token_allows_clock_skew_within_leeway() -> None:
    client = OidcClient(_settings())
    now = datetime.now(UTC)
    token = _id_token(
        nonce="n1",
        iat=now + timedelta(seconds=20),  # IdP clock 20s ahead of the backend
        exp=now + timedelta(minutes=5),
    )
    claims = client.verify_id_token(token, nonce="n1", jwks=_jwks())
    assert claims["sub"] == "user-1"


def test_json_parse_maps_malformed_body_to_503() -> None:
    client = OidcClient(_settings())
    response = httpx.Response(200, content=b"<html>not json</html>")
    with pytest.raises(AuthError) as exc:
        client._json(response)
    assert exc.value.status_code == 503
    assert exc.value.code == "OIDC_UNAVAILABLE"


@pytest.mark.asyncio
async def test_discover_uses_discovery_url_not_issuer() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "issuer": _ISSUER,
                "authorization_endpoint": _ISSUER + "/authorize",
                "token_endpoint": _ISSUER + "/token",
                "userinfo_endpoint": _ISSUER + "/userinfo",
                "jwks_uri": _ISSUER + "/jwks",
            },
        )

    settings = Settings(
        _env_file=None,
        oidc_issuer=_ISSUER,
        oidc_discovery_url="http://idp-internal:4000",
        oidc_client_id=_CLIENT_ID,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_http:
        client = OidcClient(settings, client=mock_http)
        config = await client.discover()

    assert config["issuer"] == _ISSUER
    assert requested == ["http://idp-internal:4000/.well-known/openid-configuration"]


def test_issue_access_token_roundtrip() -> None:
    settings = _settings()
    token, expires_in = issue_access_token(
        settings, sub="user-1", tenant_id="tenant-1", role="employee"
    )
    claims = jwt.decode(token, _PUBLIC_PEM, algorithms=["RS256"])
    assert expires_in == 900
    assert claims["sub"] == "user-1"
    assert claims["tenantId"] == "tenant-1"
    assert claims["role"] == "employee"
    assert claims["jti"]
    assert claims["exp"] > claims["iat"]


def test_me_requires_authentication(client: TestClient) -> None:
    resp = client.get("/api/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_refresh_requires_cookie(client: TestClient) -> None:
    resp = client.post("/api/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_logout_without_cookie_is_idempotent(client: TestClient) -> None:
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"data": {"message": "已登出"}}


# ── Callback-chain integration tests (fake IdP over httpx.MockTransport) ──


class _FakeIdP:
    """Minimal OIDC provider served through ``httpx.MockTransport``."""

    def __init__(self) -> None:
        self.nonce: str | None = None
        self.token_status = 200
        # When set, overrides the token endpoint's normal response (failure injection).
        self.token_body: dict[str, object] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": _ISSUER,
                    "authorization_endpoint": _ISSUER + "/authorize",
                    "token_endpoint": _ISSUER + "/token",
                    "userinfo_endpoint": _ISSUER + "/userinfo",
                    "jwks_uri": _ISSUER + "/jwks",
                },
            )
        if path == "/jwks":
            return httpx.Response(200, json=_jwks())
        if path == "/token":
            if self.token_status != 200 or self.token_body is not None:
                return httpx.Response(self.token_status, json=self.token_body or {})
            return httpx.Response(
                200,
                json={
                    "id_token": _id_token(nonce=self.nonce or "n"),
                    "access_token": "idp-access",
                    "token_type": "Bearer",
                },
            )
        if path == "/userinfo":
            return httpx.Response(
                200,
                json={"sub": "user-1", "email": "alice@acme.com", "name": "Alice"},
            )
        return httpx.Response(404)


async def _start_callback_flow(
    idp: _FakeIdP, session_factory
) -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
    """Seed a tenant, wire the app to the fake IdP, return (http, mock_http)."""
    async with session_factory() as session:
        session.add(Tenant(name="Acme", slug="acme", sso_domain="acme.com"))
        await session.commit()

    settings = _settings()
    app = create_app(settings)
    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(idp))
    app.state.oidc = OidcClient(settings, client=mock_http)
    app.state.state_store = OidcStateStore(FakeRedis())  # type: ignore[arg-type]
    app.state.refresh_service = RefreshService(settings)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    http = httpx.AsyncClient(transport=transport, base_url="http://test")
    return http, mock_http


async def _begin_login(http: httpx.AsyncClient) -> tuple[str, str]:
    """GET /api/auth/login and return ``(state, nonce)`` from the redirect."""
    login = await http.get("/api/auth/login", follow_redirects=False)
    assert login.status_code == 302, login.text
    query = urllib.parse.parse_qs(urllib.parse.urlparse(login.headers["location"]).query)
    assert query["code_challenge_method"][0] == "S256"
    return query["state"][0], query["nonce"][0]


@pytest.mark.asyncio
async def test_callback_chain_success(session_factory) -> None:  # type: ignore[no-untyped-def]
    idp = _FakeIdP()
    http, mock_http = await _start_callback_flow(idp, session_factory)
    try:
        state, nonce = await _begin_login(http)
        idp.nonce = nonce

        callback = await http.get(
            f"/api/auth/callback?code=c1&state={state}", follow_redirects=False
        )
        assert callback.status_code == 302, callback.text
        assert callback.headers["location"] == "/user"
        assert "refresh_token" in http.cookies

        # The callback delivers no body — the access token arrives via refresh.
        refresh = await http.post("/api/auth/refresh")
        assert refresh.status_code == 200, refresh.text
        data = refresh.json()["data"]
        assert data["expiresIn"] == 900
        assert data["accessToken"]

        # The rotated access token authenticates a protected endpoint.
        me = await http.get("/api/me", headers={"Authorization": f"Bearer {data['accessToken']}"})
        assert me.status_code == 200, me.text
        assert me.json()["data"]["email"] == "alice@acme.com"
    finally:
        await http.aclose()
        await mock_http.aclose()


@pytest.mark.asyncio
async def test_callback_malformed_id_token_returns_400(session_factory) -> None:  # type: ignore[no-untyped-def]
    idp = _FakeIdP()
    idp.token_body = {"id_token": "not-a-jwt", "access_token": "idp-access"}
    http, mock_http = await _start_callback_flow(idp, session_factory)
    try:
        state, nonce = await _begin_login(http)
        idp.nonce = nonce

        callback = await http.get(
            f"/api/auth/callback?code=c1&state={state}", follow_redirects=False
        )
        assert callback.status_code == 400, callback.text
        assert callback.json()["error"]["code"] == "INVALID_ID_TOKEN"
    finally:
        await http.aclose()
        await mock_http.aclose()


@pytest.mark.asyncio
async def test_callback_exchange_failure_returns_400(session_factory) -> None:  # type: ignore[no-untyped-def]
    idp = _FakeIdP()
    idp.token_status = 400
    idp.token_body = {"error": "invalid_grant"}
    http, mock_http = await _start_callback_flow(idp, session_factory)
    try:
        state, nonce = await _begin_login(http)
        idp.nonce = nonce

        callback = await http.get(
            f"/api/auth/callback?code=c1&state={state}", follow_redirects=False
        )
        assert callback.status_code == 400, callback.text
        assert callback.json()["error"]["code"] == "OIDC_TOKEN_EXCHANGE_FAILED"
    finally:
        await http.aclose()
        await mock_http.aclose()


@pytest.mark.asyncio
async def test_callback_state_replay_returns_400(session_factory) -> None:  # type: ignore[no-untyped-def]
    idp = _FakeIdP()
    http, mock_http = await _start_callback_flow(idp, session_factory)
    try:
        state, nonce = await _begin_login(http)
        idp.nonce = nonce

        first = await http.get(f"/api/auth/callback?code=c1&state={state}", follow_redirects=False)
        assert first.status_code == 302, first.text

        replay = await http.get(f"/api/auth/callback?code=c1&state={state}", follow_redirects=False)
        assert replay.status_code == 400, replay.text
        assert replay.json()["error"]["code"] == "INVALID_STATE"
    finally:
        await http.aclose()
        await mock_http.aclose()


# ── SSO end-to-end contract regression (UC-01-1 / UC-01-4) ──
#
# These assert the HTTP-level contract the frontend relies on, so a regression
# in the OIDC wiring surfaces here without a real browser. The "Max-Age=900"
# wording in the original gap conflates two distinct values: ``expiresIn: 900``
# is the access-token TTL returned in the refresh response BODY, while the
# refresh cookie itself carries the 30-day refresh TTL (2592000s).


def _set_cookie_attrs(response: httpx.Response) -> dict[str, str]:
    """Parse a response's ``Set-Cookie`` header into a lowercase attribute map.

    Flags without a value (``HttpOnly``) map to ``"true"``; valued attributes
    keep their raw string. The cookie's name/value are exposed as ``name`` and
    ``value``.
    """
    header = response.headers.get("set-cookie")
    assert header, "expected a Set-Cookie header"
    segments = [segment.strip() for segment in header.split(";")]
    name, _, value = segments[0].partition("=")
    attrs: dict[str, str] = {"name": name, "value": value}
    for segment in segments[1:]:
        key, sep, val = segment.partition("=")
        attrs[key.lower()] = val if sep else "true"
    return attrs


def _assert_refresh_cookie(attrs: dict[str, str]) -> None:
    """Assert the refresh-cookie contract the frontend depends on."""
    assert attrs["name"] == "refresh_token"
    assert attrs["value"]
    assert attrs.get("httponly") == "true"
    assert attrs.get("path") == "/api/auth"
    assert attrs.get("samesite") == "lax"
    # 30-day refresh TTL — NOT the 900s access-token TTL (see module note).
    assert attrs.get("max-age") == "2592000"


@pytest.mark.asyncio
async def test_callback_sets_refresh_cookie_contract(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Callback issues the refresh cookie with the documented attributes."""
    idp = _FakeIdP()
    http, mock_http = await _start_callback_flow(idp, session_factory)
    try:
        state, nonce = await _begin_login(http)
        idp.nonce = nonce

        callback = await http.get(
            f"/api/auth/callback?code=c1&state={state}", follow_redirects=False
        )
        assert callback.status_code == 302, callback.text
        assert callback.headers["location"] == "/user"
        _assert_refresh_cookie(_set_cookie_attrs(callback))
    finally:
        await http.aclose()
        await mock_http.aclose()


@pytest.mark.asyncio
async def test_refresh_rotates_cookie_with_contract(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Refresh mints a fresh cookie (same attributes, new value) + ``expiresIn: 900``."""
    idp = _FakeIdP()
    http, mock_http = await _start_callback_flow(idp, session_factory)
    try:
        state, nonce = await _begin_login(http)
        idp.nonce = nonce
        await http.get(f"/api/auth/callback?code=c1&state={state}", follow_redirects=False)
        original = http.cookies.get("refresh_token")
        assert original

        refresh = await http.post("/api/auth/refresh")
        assert refresh.status_code == 200, refresh.text
        data = refresh.json()["data"]
        assert data["expiresIn"] == 900
        assert data["accessToken"]

        rotated = _set_cookie_attrs(refresh)
        _assert_refresh_cookie(rotated)
        assert rotated["value"] != original  # the cookie actually rotated
    finally:
        await http.aclose()
        await mock_http.aclose()


@pytest.mark.asyncio
async def test_logout_revokes_family_and_clears_cookie(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Logout clears the cookie and revokes the family: the old cookie is dead."""
    idp = _FakeIdP()
    http, mock_http = await _start_callback_flow(idp, session_factory)
    try:
        state, nonce = await _begin_login(http)
        idp.nonce = nonce
        await http.get(f"/api/auth/callback?code=c1&state={state}", follow_redirects=False)
        await http.post("/api/auth/refresh")
        active = http.cookies.get("refresh_token")
        assert active

        logout = await http.post("/api/auth/logout")
        assert logout.status_code == 200, logout.text
        clear = _set_cookie_attrs(logout)
        assert clear["name"] == "refresh_token"
        assert clear["max-age"] == "0"

        # The revoked family no longer authenticates: replaying the active
        # cookie after logout yields 401 (TOKEN_REUSE_DETECTED).
        http.cookies.clear()
        replay = await http.post(
            "/api/auth/refresh",
            headers={"Cookie": f"refresh_token={active}"},
        )
        assert replay.status_code == 401, replay.text
    finally:
        await http.aclose()
        await mock_http.aclose()
