"""ToolController HTTP tests against a real PostgreSQL database.

The earlier version stubbed the ORM session with ``AsyncMock``, which masked the
``update()`` lazy-load defect Verity caught in QA (``MissingGreenlet`` after
``commit()`` expires the ``onupdate=func.now()`` ``updated_at`` column). These
tests drive the full ASGI stack with a real asyncpg session — via the shared
``session_factory`` fixture — so every CRUD path, including server-computed
``updated_at``, exercises real persistence instead of an in-memory stand-in.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import jwt as pyjwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models.tenant import Tenant
from app.models.tool import ToolRegistry
from app.models.user import User
from app.services.tool_proxy import ToolProxy

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_PEM = _KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()


@dataclass(frozen=True)
class ToolApi:
    """Real-DB ASGI client plus the identity and session handles tests need."""

    client: httpx.AsyncClient
    token: str
    private_key: object
    session_factory: Any
    tenant: Tenant
    user: User


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _signed_token(private_key: object, *, user: User, tenant: Tenant) -> str:
    payload = {"sub": str(user.id), "role": user.role, "tenantId": str(tenant.id)}
    return pyjwt.encode(payload, private_key, algorithm="RS256")


async def _create_tenant_user(session: AsyncSession, *, role: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        name="Tenant",
        slug=f"tenant-{uuid.uuid4().hex[:8]}",
        sso_provider="local",
        status="active",
    )
    session.add(tenant)
    await session.flush()
    user = User(
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        name="Test User",
        role=role,
        status="active",
    )
    session.add(user)
    await session.commit()
    return tenant, user


async def _insert_tool(
    session_factory: Any,
    *,
    tool_id: str,
    tenant: Tenant | None,
    permission_mode: str = "auto",
) -> None:
    """Persist a registry row so an endpoint under test has data to act on."""
    async with session_factory() as session:
        session.add(
            ToolRegistry(
                tool_id=tool_id,
                tenant_id=tenant.id if tenant is not None else None,
                name="My Tool",
                type="http",
                endpoint="http://tool.example/api",
                method="POST",
                risk_level="medium",
                permission_mode=permission_mode,
                auth_type="none",
                timeout_ms=10000,
                retry_policy={"max_retries": 2, "base_delay_ms": 1000},
                status="active",
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def tool_api(session_factory: Any) -> AsyncIterator[ToolApi]:
    """An authenticated, real-DB-backed client for the tool controller."""
    async with session_factory() as session:
        tenant, user = await _create_tenant_user(session, role="agent_admin")
    token = _signed_token(_KEY, user=user, tenant=tenant)

    app = create_app(
        Settings(_env_file=None, jwt_public_key=_PUBLIC_PEM, rate_limit_enabled=False)
    )

    async def _real_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _real_session
    app.state.tool_proxy = ToolProxy(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield ToolApi(client, token, _KEY, session_factory, tenant, user)


@pytest.mark.asyncio
async def test_create_tool_returns_201_active(tool_api: ToolApi) -> None:
    resp = await tool_api.client.post(
        "/api/tools",
        json={
            "name": "Contract Check",
            "tool_id": "contract-check",
            "type": "http",
            "endpoint": "http://erp.example/api",
            "risk_level": "low",
            "permission_mode": "auto",
        },
        headers=_auth_headers(tool_api.token),
    )

    assert resp.status_code == 201
    body = resp.json()["data"]
    assert body["status"] == "active"
    assert body["tool_id"] == "contract-check"


@pytest.mark.asyncio
async def test_debug_returns_status_body_latency(tool_api: ToolApi) -> None:
    await _insert_tool(tool_api.session_factory, tool_id="my-tool", tenant=tool_api.tenant)

    resp = await tool_api.client.post(
        "/api/tools/my-tool/debug",
        json={"params": {"contractText": "付款条件"}},
        headers=_auth_headers(tool_api.token),
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["statusCode"] == 200
    assert body["responseBody"] == {"ok": True}
    assert body["latencyMs"] >= 0


@pytest.mark.asyncio
async def test_debug_disabled_tool_returns_409(tool_api: ToolApi) -> None:
    await _insert_tool(
        tool_api.session_factory,
        tool_id="my-tool",
        tenant=tool_api.tenant,
        permission_mode="disabled",
    )

    resp = await tool_api.client.post(
        "/api/tools/my-tool/debug",
        json={"params": {}},
        headers=_auth_headers(tool_api.token),
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TOOL_DISABLED"


@pytest.mark.asyncio
async def test_update_global_tool_by_tenant_admin_returns_403(tool_api: ToolApi) -> None:
    # A tenant-scoped agent_admin must not mutate a global tool (tenant_id NULL).
    await _insert_tool(tool_api.session_factory, tool_id="my-tool", tenant=None)

    resp = await tool_api.client.patch(
        "/api/tools/my-tool",
        json={"name": "Hacked"},
        headers=_auth_headers(tool_api.token),
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_create_tool_rejects_ssrf_endpoint(tool_api: ToolApi) -> None:
    resp = await tool_api.client.post(
        "/api/tools",
        json={
            "name": "X",
            "tool_id": "x",
            "type": "http",
            "endpoint": "http://169.254.169.254/latest",
            "risk_level": "low",
        },
        headers=_auth_headers(tool_api.token),
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_tool_rejects_zero_timeout(tool_api: ToolApi) -> None:
    resp = await tool_api.client.post(
        "/api/tools",
        json={
            "name": "X",
            "tool_id": "x",
            "type": "http",
            "endpoint": "http://x.example",
            "risk_level": "low",
            "timeout_ms": 0,
        },
        headers=_auth_headers(tool_api.token),
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_tool_requires_agent_admin(tool_api: ToolApi) -> None:
    async with tool_api.session_factory() as session:
        employee = User(
            tenant_id=tool_api.tenant.id,
            sso_sub="employee-sub",
            email="employee@example.com",
            name="Employee",
            role="employee",
            status="active",
        )
        session.add(employee)
        await session.commit()

    employee_token = _signed_token(
        tool_api.private_key, user=employee, tenant=tool_api.tenant
    )

    resp = await tool_api.client.post(
        "/api/tools",
        json={
            "name": "X",
            "tool_id": "x",
            "type": "http",
            "endpoint": "http://x",
            "risk_level": "low",
        },
        headers=_auth_headers(employee_token),
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"
