"""DashboardController HTTP tests: role gating and response envelope."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models.tenant import Tenant
from app.models.user import User
from app.services.dashboard import DashboardService


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


def _make_user(tenant: Tenant, role: str = "employee") -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
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


def _signed_token(private_key: object, user: User, tenant: Tenant) -> str:
    payload = {"sub": str(user.id), "role": user.role, "tenantId": str(tenant.id)}
    return pyjwt.encode(payload, private_key, algorithm="RS256")


def _make_session(user: User) -> AsyncMock:
    session = AsyncMock()

    async def _get(model: object, pk: object, **kwargs: object) -> object | None:
        if model is User:
            return user
        return None

    session.get = AsyncMock(side_effect=_get)
    return session


def _make_client(
    user: User, tenant: Tenant, session: AsyncMock, monkeypatch
) -> tuple[TestClient, str]:
    private_key, public_pem = _keypair()
    token = _signed_token(private_key, user, tenant)

    async def _override_session() -> AsyncMock:
        return session

    app: FastAPI = create_app(
        Settings(_env_file=None, jwt_public_key=public_pem, rate_limit_enabled=False)
    )
    app.dependency_overrides[get_session] = _override_session
    return TestClient(app, raise_server_exceptions=False), token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_admin_dashboard_requires_admin_role(monkeypatch) -> None:
    tenant = _make_tenant()
    user = _make_user(tenant, role="employee")
    client, token = _make_client(user, tenant, _make_session(user), monkeypatch)

    resp = client.get("/api/dashboard/admin", headers=_auth(token))

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_admin_dashboard_returns_data_for_admin(monkeypatch) -> None:
    tenant = _make_tenant()
    user = _make_user(tenant, role="agent_admin")

    async def _fake_admin_dashboard(self, tenant_id):
        return {
            "metrics": {"totalAgents": 3, "todayCalls": 7, "avgLatencyMs": 120, "errorRate": 0.0},
            "trend": [],
            "topAgents": [],
        }

    monkeypatch.setattr(DashboardService, "get_admin_dashboard", _fake_admin_dashboard)
    client, token = _make_client(user, tenant, _make_session(user), monkeypatch)

    resp = client.get("/api/dashboard/admin", headers=_auth(token))

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["metrics"]["totalAgents"] == 3
    assert body["metrics"]["todayCalls"] == 7
    assert body["trend"] == []


def test_user_home_returns_data_for_employee(monkeypatch) -> None:
    tenant = _make_tenant()
    user = _make_user(tenant, role="employee")

    async def _fake_user_home(self, tenant_id, user_id):
        return {"recommendedAgents": [], "recentConversations": [], "pendingTaskCount": 2}

    monkeypatch.setattr(DashboardService, "get_user_home", _fake_user_home)
    client, token = _make_client(user, tenant, _make_session(user), monkeypatch)

    resp = client.get("/api/dashboard/user", headers=_auth(token))

    assert resp.status_code == 200
    assert resp.json()["data"]["pendingTaskCount"] == 2


def test_user_home_requires_authentication(monkeypatch) -> None:
    tenant = _make_tenant()
    user = _make_user(tenant, role="employee")
    client, _ = _make_client(user, tenant, _make_session(user), monkeypatch)

    resp = client.get("/api/dashboard/user")

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
