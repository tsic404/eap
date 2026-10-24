"""Shared harness for the member-management acceptance cases.

TC-tech-auth-05 ~ 09 all drive the real ASGI stack against PostgreSQL: they boot
the app with a request-scoped session bound to the test database, seed tenants
and members, mint access tokens with the app's own signing key and read the
contract's observable results (status, error code, body, cookie).

Kept in one place so the five cases judge the same wiring — only the scenario
differs between them.
"""

from __future__ import annotations

import base64
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.local_login import LocalLoginService
from app.auth.login_lockout import InMemoryLoginAttempts
from app.auth.oidc import OidcClient
from app.auth.password import hash_password
from app.auth.refresh import RefreshService
from app.auth.state_store import OidcStateStore
from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models.tenant import Tenant
from app.models.user import ACCOUNT_SOURCE_LOCAL, ACCOUNT_SOURCE_SSO, User
from tests.conftest import unique_client_ip

LOGIN_PATH = "/api/auth/login/local"
CAPABILITY_PATH = "/api/auth/capabilities"
CHANGE_PASSWORD_PATH = "/api/auth/password"
REFRESH_PATH = "/api/auth/refresh"
LOGOUT_PATH = "/api/auth/logout"
MEMBERS_PATH = "/api/users"
CALLBACK_PATH = "/api/auth/callback"
FRONTEND_HOME = "/user"
FRONTEND_LOGIN = "/login"

DEFAULT_PASSWORD = "initial-passphrase-1"
DEFAULT_DOMAIN = "acme.com"
_IDP_ISSUER = "https://sso.example.com"
_IDP_CLIENT_ID = "client-123"
# Only the rate-limited login path is steered by a bucket, and every case gets
# its own client address so cases never spend each other's budget (C-10).
_ROOMY_LOGIN_LIMIT = 100

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


def access_token(user: User, tenant: Tenant, *, jti: str | None = None) -> str:
    """Mint the access token the identity middleware accepts for ``user``."""
    payload: dict[str, object] = {
        "sub": str(user.id),
        "role": user.role,
        "tenantId": str(tenant.id),
    }
    if jti is not None:
        payload["jti"] = jti
    return pyjwt.encode(payload, _KEY, algorithm="RS256")


def auth(token: str) -> dict[str, str]:
    """Bearer header for an access token."""
    return {"Authorization": f"Bearer {token}"}


def error_code(response: httpx.Response) -> str:
    """The contract's machine-readable code from an error envelope (C-5)."""
    return str(response.json()["error"]["code"])


def error_details(response: httpx.Response) -> list[dict[str, Any]]:
    """The per-field ``details`` array of a 422 answer (C-5)."""
    return list(response.json()["error"].get("details") or [])


def refresh_cookie(response: httpx.Response) -> str:
    """The value of the refresh cookie a response set (C-3)."""
    header = response.headers.get("set-cookie")
    assert header, "expected a Set-Cookie header"
    name, _, value = header.split(";", 1)[0].partition("=")
    assert name == "refresh_token", header
    return value


@dataclass
class Harness:
    """A running app plus the client that drives it."""

    app: Any
    http: httpx.AsyncClient

    async def close(self) -> None:
        await self.http.aclose()


async def start(
    session_factory: Any,
    *,
    local_login_enabled: bool = True,
    idp: FakeIdp | None = None,
) -> Harness:
    """Boot the app against the test database and return a client for it."""
    settings = Settings(
        _env_file=None,
        local_login_enabled=local_login_enabled,
        rate_limit_login_per_minute=_ROOMY_LOGIN_LIMIT,
        jwt_private_key=_PRIVATE_PEM,
        jwt_public_key=_PUBLIC_PEM,
        oidc_issuer=_IDP_ISSUER if idp is not None else "",
        oidc_client_id=_IDP_CLIENT_ID,
    )
    app = create_app(settings)
    app.state.refresh_service = RefreshService(settings)
    # The lifespan does not run under an ASGI transport, so the local-login
    # service (and the attempt counter it reads) is wired here, exactly as the
    # container does at startup.
    app.state.login_attempts = InMemoryLoginAttempts()
    app.state.local_login_service = LocalLoginService(
        settings, limiter=app.state.rate_limiter, attempts=app.state.login_attempts
    )

    async def override_get_session() -> Any:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    idp_http: httpx.AsyncClient | None = None
    if idp is not None:
        idp_http = httpx.AsyncClient(transport=httpx.MockTransport(idp))
        from tests.conftest import FakeRedis

        fake_redis = FakeRedis()
        app.state.redis = fake_redis
        app.state.oidc = OidcClient(settings, client=idp_http)
        app.state.state_store = OidcStateStore(fake_redis)  # type: ignore[arg-type]

    transport = httpx.ASGITransport(
        app=app, raise_app_exceptions=False, client=(unique_client_ip(), 1234)
    )
    return Harness(app, httpx.AsyncClient(transport=transport, base_url="http://test"))


async def seed_tenant(
    session_factory: Any, *, domain: str | None = DEFAULT_DOMAIN, name: str = "Acme"
) -> Tenant:
    """Seed a tenant, by default owning the ``acme.com`` email domain."""
    async with session_factory() as session:
        tenant = Tenant(
            name=name,
            slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}",
            sso_domain=domain,
        )
        session.add(tenant)
        await session.commit()
        return tenant


async def seed_user(
    session_factory: Any,
    tenant: Tenant,
    *,
    email: str,
    name: str = "Member",
    role: str = "employee",
    status: str = "active",
    password: str | None = None,
    must_change_password: bool = False,
    department: str | None = None,
    sso_sub: str | None = None,
) -> User:
    """Seed one member: a local account when a password is given, else SSO."""
    account_source = ACCOUNT_SOURCE_LOCAL if password is not None else ACCOUNT_SOURCE_SSO
    generated = (
        f"local:{uuid.uuid4()}" if account_source == ACCOUNT_SOURCE_LOCAL else f"sub-{uuid.uuid4()}"
    )
    async with session_factory() as session:
        user = User(
            tenant_id=tenant.id,
            sso_sub=sso_sub or generated,
            email=email,
            name=name,
            role=role,
            status=status,
            department=department,
            password_hash=hash_password(password) if password else None,
            must_change_password=must_change_password,
            account_source=account_source,
        )
        session.add(user)
        await session.commit()
        return user


async def seed_admin(
    session_factory: Any, tenant: Tenant, *, email: str = "admin@acme.com"
) -> User:
    """Seed the tenant's platform administrator (an SSO account)."""
    return await seed_user(
        session_factory, tenant, email=email, name="Admin", role="platform_admin"
    )


async def count_rows(session_factory: Any, statement: Any) -> int:
    """Run a scalar count against the test database."""
    async with session_factory() as session:
        return int(await session.scalar(statement) or 0)


class FakeIdp:
    """Minimal OIDC provider served through ``httpx.MockTransport``."""

    def __init__(self, *, sub: str, email: str, name: str = "Enterprise User") -> None:
        self.sub = sub
        self.email = email
        self.name = name
        self.nonce: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": _IDP_ISSUER,
                    "authorization_endpoint": _IDP_ISSUER + "/authorize",
                    "token_endpoint": _IDP_ISSUER + "/token",
                    "userinfo_endpoint": _IDP_ISSUER + "/userinfo",
                    "jwks_uri": _IDP_ISSUER + "/jwks",
                },
            )
        if path == "/jwks":
            return httpx.Response(200, json=_jwks())
        if path == "/token":
            return httpx.Response(
                200,
                json={
                    "id_token": self._id_token(),
                    "access_token": "idp-access",
                    "token_type": "Bearer",
                },
            )
        if path == "/userinfo":
            return httpx.Response(
                200, json={"sub": self.sub, "email": self.email, "name": self.name}
            )
        return httpx.Response(404)

    def _id_token(self) -> str:
        now = datetime.now(UTC)
        payload: dict[str, object] = {
            "iss": _IDP_ISSUER,
            "aud": _IDP_CLIENT_ID,
            "sub": self.sub,
            "nonce": self.nonce or "n",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        }
        return pyjwt.encode(payload, _KEY, algorithm="RS256", headers={"kid": "test-key"})


def _int_to_b64url(number: int) -> str:
    length = (number.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(number.to_bytes(length, "big")).rstrip(b"=").decode()


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


async def begin_login(http: httpx.AsyncClient, idp: FakeIdp | None = None) -> str:
    """GET /api/auth/login, bind the issued nonce, and return the state."""
    login = await http.get("/api/auth/login", follow_redirects=False)
    assert login.status_code == 302, login.text
    query = urllib.parse.parse_qs(urllib.parse.urlparse(login.headers["location"]).query)
    if idp is not None:
        idp.nonce = query["nonce"][0]
    return query["state"][0]
