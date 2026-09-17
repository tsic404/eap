"""ToolController HTTP tests (stubbed session + MockTransport proxy)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import httpx
import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models.tenant import Tenant
from app.models.tool import ToolRegistry
from app.models.user import User
from app.services.tool_proxy import ToolProxy


def _make_tenant() -> Tenant:
    return Tenant(
        id=uuid.uuid4(),
        name="Tenant",
        slug=f"tenant-{uuid.uuid4().hex[:8]}",
        sso_provider="local",
        status="active",
        quota_limit=10000,
        quota_used=0,
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


def _make_tool(**overrides: object) -> ToolRegistry:
    now = datetime.now(UTC)
    fields: dict[str, object] = {
        "tool_id": "my-tool",
        "tenant_id": None,
        "name": "My Tool",
        "type": "http",
        "endpoint": "http://tool.example/api",
        "method": "POST",
        "risk_level": "medium",
        "permission_mode": "auto",
        "auth_type": "none",
        "auth_config": None,
        "timeout_ms": 10000,
        "retry_policy": {"max_retries": 2, "base_delay_ms": 1000},
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return ToolRegistry(**fields)  # type: ignore[arg-type]


def _keypair() -> tuple[object, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_key, public_pem


def _signed_token(private_key: object, user: User, tenant: Tenant) -> str:
    payload = {"sub": str(user.id), "role": user.role, "tenantId": str(tenant.id)}
    return pyjwt.encode(payload, private_key, algorithm="RS256")


def _make_client(tool: ToolRegistry | None = None) -> tuple[TestClient, str, AsyncMock]:
    """Build an app with a stubbed session and a MockTransport-backed ToolProxy."""
    private_key, public_pem = _keypair()
    tenant = _make_tenant()
    user = _make_user(tenant)
    token = _signed_token(private_key, user, tenant)

    tools: dict[str, ToolRegistry] = {tool.tool_id: tool} if tool else {}

    session = AsyncMock()
    session.add = Mock()
    session.delete = AsyncMock()

    async def _get(model: object, pk: object) -> object | None:
        if model is User:
            return user
        if model is Tenant:
            return tenant
        if model is ToolRegistry:
            return tools.get(str(pk))
        return None

    session.get = AsyncMock(side_effect=_get)

    async def _refresh(obj: object) -> None:
        if isinstance(obj, ToolRegistry):
            if obj.created_at is None:
                now = datetime.now(UTC)
                obj.created_at = now
                obj.updated_at = now

    session.refresh = AsyncMock(side_effect=_refresh)

    async def _override_session() -> AsyncMock:
        return session

    app = create_app(
        Settings(_env_file=None, jwt_public_key=public_pem, rate_limit_enabled=False)
    )
    app.dependency_overrides[get_session] = _override_session
    app.state.tool_proxy = ToolProxy(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
    )
    return TestClient(app, raise_server_exceptions=False), token, session


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_tool_returns_201_active() -> None:
    client, token, _ = _make_client()

    resp = client.post(
        "/api/tools",
        json={
            "name": "Contract Check",
            "tool_id": "contract-check",
            "type": "http",
            "endpoint": "http://erp.example/api",
            "risk_level": "low",
            "permission_mode": "auto",
        },
        headers=_auth_headers(token),
    )

    assert resp.status_code == 201
    body = resp.json()["data"]
    assert body["status"] == "active"
    assert body["tool_id"] == "contract-check"


def test_debug_returns_status_body_latency() -> None:
    client, token, _ = _make_client(_make_tool())

    resp = client.post(
        "/api/tools/my-tool/debug",
        json={"params": {"contractText": "付款条件"}},
        headers=_auth_headers(token),
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["statusCode"] == 200
    assert body["responseBody"] == {"ok": True}
    assert body["latencyMs"] >= 0


def test_debug_disabled_tool_returns_409() -> None:
    client, token, _ = _make_client(_make_tool(permission_mode="disabled"))

    resp = client.post(
        "/api/tools/my-tool/debug",
        json={"params": {}},
        headers=_auth_headers(token),
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TOOL_DISABLED"


def test_update_global_tool_by_tenant_admin_returns_403() -> None:
    # A tenant-scoped agent_admin must not mutate a global tool (tenant_id NULL).
    client, token, _ = _make_client(_make_tool())

    resp = client.patch(
        "/api/tools/my-tool",
        json={"name": "Hacked"},
        headers=_auth_headers(token),
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_create_tool_rejects_ssrf_endpoint() -> None:
    client, token, _ = _make_client()

    resp = client.post(
        "/api/tools",
        json={
            "name": "X",
            "tool_id": "x",
            "type": "http",
            "endpoint": "http://169.254.169.254/latest",
            "risk_level": "low",
        },
        headers=_auth_headers(token),
    )

    assert resp.status_code == 422


def test_create_tool_rejects_zero_timeout() -> None:
    client, token, _ = _make_client()

    resp = client.post(
        "/api/tools",
        json={
            "name": "X",
            "tool_id": "x",
            "type": "http",
            "endpoint": "http://x.example",
            "risk_level": "low",
            "timeout_ms": 0,
        },
        headers=_auth_headers(token),
    )

    assert resp.status_code == 422


def test_create_tool_requires_agent_admin() -> None:
    private_key, public_pem = _keypair()
    tenant = _make_tenant()
    employee = _make_user(tenant, role="employee")
    token = _signed_token(private_key, employee, tenant)

    session = AsyncMock()
    session.add = Mock()
    session.delete = AsyncMock()

    async def _get(model: object, pk: object) -> object | None:
        if model is User:
            return employee
        if model is Tenant:
            return tenant
        return None

    session.get = AsyncMock(side_effect=_get)

    async def _override_session() -> AsyncMock:
        return session

    app = create_app(
        Settings(_env_file=None, jwt_public_key=public_pem, rate_limit_enabled=False)
    )
    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/tools",
        json={
            "name": "X",
            "tool_id": "x",
            "type": "http",
            "endpoint": "http://x",
            "risk_level": "low",
        },
        headers=_auth_headers(token),
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"
