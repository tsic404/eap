"""RBAC dependency tests: get_current_user, require_roles, get_active_tenant."""

import uuid
from unittest.mock import AsyncMock

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_session
from app.dependencies import get_active_tenant, get_current_user, require_roles
from app.main import create_app
from app.models.tenant import Tenant
from app.models.user import User


def _make_tenant(
    *, status: str = "active", quota_limit: int = 10000, quota_used: int = 0
) -> Tenant:
    return Tenant(
        id=uuid.uuid4(),
        name="Tenant",
        slug=f"tenant-{uuid.uuid4().hex[:8]}",
        sso_provider="local",
        status=status,
        quota_limit=quota_limit,
        quota_used=quota_used,
    )


def _make_user(tenant: Tenant, role: str = "agent_admin") -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        sso_sub="test-sub",
        email="user@example.com",
        name="Test User",
        role=role,
        status="active",
    )


def _keypair() -> tuple[object, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_key, public_pem


def _signed_token(private_key: object, user: User, tenant: Tenant | None = None) -> str:
    payload: dict[str, str] = {"sub": str(user.id), "role": user.role}
    if tenant is not None:
        payload["tenantId"] = str(tenant.id)
    return pyjwt.encode(payload, private_key, algorithm="RS256")


def _client(
    user: User | None, tenant: Tenant | None, private_key: object, public_pem: str
) -> TestClient:
    """Build an app whose DB dependency is stubbed with the given rows."""
    app: FastAPI = create_app(
        settings=Settings(_env_file=None, jwt_public_key=public_pem, rate_limit_enabled=False)
    )
    session = AsyncMock()

    async def _get(model: object, pk: object) -> object | None:
        if model is User:
            return user
        if model is Tenant:
            return tenant
        return None

    session.get = AsyncMock(side_effect=_get)

    async def _override_session() -> AsyncMock:
        return session

    app.dependency_overrides[get_session] = _override_session
    return TestClient(app, raise_server_exceptions=False)


def test_get_current_user_returns_401_without_jwt() -> None:
    private_key, public_pem = _keypair()
    tenant = _make_tenant()
    client = _client(_make_user(tenant), tenant, private_key, public_pem)

    @client.app.get("/api/test/me")  # type: ignore[attr-defined]
    async def me(current_user: User = Depends(get_current_user)) -> dict[str, str]:
        return {"role": current_user.role}

    resp = client.get("/api/test/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_get_current_user_returns_user_when_authenticated() -> None:
    private_key, public_pem = _keypair()
    tenant = _make_tenant()
    user = _make_user(tenant, role="agent_admin")
    client = _client(user, tenant, private_key, public_pem)
    token = _signed_token(private_key, user, tenant)

    @client.app.get("/api/test/me")  # type: ignore[attr-defined]
    async def me(current_user: User = Depends(get_current_user)) -> dict[str, str]:
        return {"role": current_user.role}

    resp = client.get("/api/test/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"data": {"role": "agent_admin"}}


def test_get_current_user_returns_401_when_user_unknown() -> None:
    private_key, public_pem = _keypair()
    tenant = _make_tenant()
    user = _make_user(tenant)
    client = _client(None, tenant, private_key, public_pem)
    token = _signed_token(private_key, user, tenant)

    @client.app.get("/api/test/me")  # type: ignore[attr-defined]
    async def me(current_user: User = Depends(get_current_user)) -> dict[str, str]:
        return {"role": current_user.role}

    resp = client.get("/api/test/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_require_roles_returns_403_for_employee() -> None:
    private_key, public_pem = _keypair()
    tenant = _make_tenant()
    user = _make_user(tenant, role="employee")
    client = _client(user, tenant, private_key, public_pem)
    token = _signed_token(private_key, user, tenant)

    @client.app.post("/api/test/agents")  # type: ignore[attr-defined]
    async def create_agent(current_user: User = Depends(require_roles("agent_admin"))) -> dict:
        return {"ok": True}

    resp = client.post("/api/test/agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_require_roles_allows_matching_role() -> None:
    private_key, public_pem = _keypair()
    tenant = _make_tenant()
    user = _make_user(tenant, role="agent_admin")
    client = _client(user, tenant, private_key, public_pem)
    token = _signed_token(private_key, user, tenant)

    @client.app.post("/api/test/agents")  # type: ignore[attr-defined]
    async def create_agent(current_user: User = Depends(require_roles("agent_admin"))) -> dict:
        return {"ok": True}

    resp = client.post("/api/test/agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"data": {"ok": True}}


def test_get_active_tenant_returns_403_when_suspended() -> None:
    private_key, public_pem = _keypair()
    tenant = _make_tenant(status="suspended")
    user = _make_user(tenant)
    client = _client(user, tenant, private_key, public_pem)
    token = _signed_token(private_key, user, tenant)

    @client.app.get("/api/test/tenant")  # type: ignore[attr-defined]
    async def tenant_info(active_tenant: Tenant = Depends(get_active_tenant)) -> dict:
        return {"slug": active_tenant.slug}

    resp = client.get("/api/test/tenant", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "TENANT_SUSPENDED"


def test_get_active_tenant_returns_429_when_over_quota() -> None:
    private_key, public_pem = _keypair()
    tenant = _make_tenant(quota_limit=100, quota_used=100)
    user = _make_user(tenant)
    client = _client(user, tenant, private_key, public_pem)
    token = _signed_token(private_key, user, tenant)

    @client.app.get("/api/test/tenant")  # type: ignore[attr-defined]
    async def tenant_info(active_tenant: Tenant = Depends(get_active_tenant)) -> dict:
        return {"slug": active_tenant.slug}

    resp = client.get("/api/test/tenant", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "QUOTA_EXCEEDED"


def test_get_active_tenant_returns_tenant_when_ok() -> None:
    private_key, public_pem = _keypair()
    tenant = _make_tenant()
    user = _make_user(tenant)
    client = _client(user, tenant, private_key, public_pem)
    token = _signed_token(private_key, user, tenant)

    @client.app.get("/api/test/tenant")  # type: ignore[attr-defined]
    async def tenant_info(active_tenant: Tenant = Depends(get_active_tenant)) -> dict:
        return {"slug": active_tenant.slug}

    resp = client.get("/api/test/tenant", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"data": {"slug": tenant.slug}}


def test_get_active_tenant_requires_auth_even_with_x_tenant_id() -> None:
    # Regression: the untrusted X-Tenant-Id header must not resolve a tenant for
    # an anonymous caller (get_current_user rejects before tenant resolution).
    private_key, public_pem = _keypair()
    tenant = _make_tenant()
    client = _client(_make_user(tenant), tenant, private_key, public_pem)

    @client.app.get("/api/test/tenant")  # type: ignore[attr-defined]
    async def tenant_info(active_tenant: Tenant = Depends(get_active_tenant)) -> dict:
        return {"slug": active_tenant.slug}

    resp = client.get("/api/test/tenant", headers={"X-Tenant-Id": str(tenant.id)})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_get_active_tenant_rejects_cross_tenant() -> None:
    # A user may only resolve their own tenant; resolving another tenant's id
    # (here via X-Tenant-Id since the JWT carries no tenantId claim) is 403.
    private_key, public_pem = _keypair()
    own_tenant = _make_tenant()
    user = _make_user(own_tenant, role="employee")
    other_tenant = _make_tenant()
    client = _client(user, other_tenant, private_key, public_pem)
    token = _signed_token(private_key, user)  # no tenantId claim

    @client.app.get("/api/test/tenant")  # type: ignore[attr-defined]
    async def tenant_info(active_tenant: Tenant = Depends(get_active_tenant)) -> dict:
        return {"slug": active_tenant.slug}

    resp = client.get(
        "/api/test/tenant",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": str(other_tenant.id)},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_get_active_tenant_rejects_unknown_tenant_before_query() -> None:
    # A non-admin requesting ANY tenant other than their own must be rejected
    # before a DB lookup: a tenant UUID that does not exist at all is still 403
    # (not 404), so callers cannot probe tenant existence.
    private_key, public_pem = _keypair()
    own_tenant = _make_tenant()
    user = _make_user(own_tenant, role="employee")
    client = _client(user, None, private_key, public_pem)  # Tenant lookup -> None
    token = _signed_token(private_key, user)  # no tenantId claim

    @client.app.get("/api/test/tenant")  # type: ignore[attr-defined]
    async def tenant_info(active_tenant: Tenant = Depends(get_active_tenant)) -> dict:
        return {"slug": active_tenant.slug}

    unknown_tenant_id = uuid.uuid4()  # neither the user's tenant nor in the DB
    resp = client.get(
        "/api/test/tenant",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": str(unknown_tenant_id)},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_get_active_tenant_requires_tenant_context() -> None:
    # Authenticated but no tenant claim and no X-Tenant-Id header -> 401.
    private_key, public_pem = _keypair()
    tenant = _make_tenant()
    user = _make_user(tenant)
    client = _client(user, tenant, private_key, public_pem)
    token = _signed_token(private_key, user)  # no tenantId claim

    @client.app.get("/api/test/tenant")  # type: ignore[attr-defined]
    async def tenant_info(active_tenant: Tenant = Depends(get_active_tenant)) -> dict:
        return {"slug": active_tenant.slug}

    resp = client.get("/api/test/tenant", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_get_active_tenant_allows_platform_admin_cross_tenant() -> None:
    private_key, public_pem = _keypair()
    own_tenant = _make_tenant()
    user = _make_user(own_tenant, role="platform_admin")
    other_tenant = _make_tenant()
    client = _client(user, other_tenant, private_key, public_pem)
    token = _signed_token(private_key, user)  # no tenantId claim

    @client.app.get("/api/test/tenant")  # type: ignore[attr-defined]
    async def tenant_info(active_tenant: Tenant = Depends(get_active_tenant)) -> dict:
        return {"slug": active_tenant.slug}

    resp = client.get(
        "/api/test/tenant",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": str(other_tenant.id)},
    )
    assert resp.status_code == 200
    assert resp.json() == {"data": {"slug": other_tenant.slug}}
