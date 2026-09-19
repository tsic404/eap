"""RunLogController HTTP tests (stubbed session + signed JWT)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models.run_log import RunLog
from app.models.tenant import Tenant
from app.models.user import User


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


def _make_user(tenant: Tenant, role: str = "auditor") -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        name="Test User",
        role=role,
        status="active",
    )


def _make_run_log(tenant: Tenant, user: User, *, trace_id: str = "trace-1") -> RunLog:
    return RunLog(
        id=uuid.uuid4(),
        trace_id=trace_id,
        tenant_id=tenant.id,
        agent_id="agent-1",
        user_id=user.id,
        agent_name="Agent",
        user_name=user.name,
        status="success",
        input="hi",
        model_name="gpt-4",
        token_usage=10,
        latency_ms=100,
        tool_call_count=0,
        knowledge_hit_count=0,
        created_at=datetime.now(UTC),
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


def _make_session(
    user: User,
    *,
    run_logs: list[RunLog] | None = None,
    detail: RunLog | None = None,
) -> AsyncMock:
    session = AsyncMock()

    async def _get(model: object, pk: object, **kwargs: object) -> object | None:
        if model is User:
            return user
        return None

    session.get = AsyncMock(side_effect=_get)

    scalars_result = AsyncMock()
    scalars_result.all = Mock(return_value=list(run_logs or []))
    session.scalars = AsyncMock(return_value=scalars_result)
    session.scalar = AsyncMock(return_value=detail)
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


def test_list_requires_auditor_role(monkeypatch):
    tenant = _make_tenant()
    user = _make_user(tenant, role="employee")
    session = _make_session(user)

    client, token = _make_client(user, tenant, session, monkeypatch)
    resp = client.get("/api/run-logs", headers=_auth(token))

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_list_returns_page_for_auditor(monkeypatch):
    tenant = _make_tenant()
    user = _make_user(tenant, role="auditor")
    run_log = _make_run_log(tenant, user)
    session = _make_session(user, run_logs=[run_log])

    client, token = _make_client(user, tenant, session, monkeypatch)
    resp = client.get("/api/run-logs", headers=_auth(token))

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["nextCursor"] is None
    assert [item["traceId"] for item in body["items"]] == ["trace-1"]


def test_detail_unknown_trace_returns_404(monkeypatch):
    tenant = _make_tenant()
    user = _make_user(tenant, role="auditor")
    session = _make_session(user, detail=None)

    client, token = _make_client(user, tenant, session, monkeypatch)
    resp = client.get("/api/run-logs/nope", headers=_auth(token))

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RUN_LOG_NOT_FOUND"


def test_detail_requires_auditor_role(monkeypatch):
    tenant = _make_tenant()
    user = _make_user(tenant, role="employee")
    session = _make_session(user)

    client, token = _make_client(user, tenant, session, monkeypatch)
    resp = client.get("/api/run-logs/trace-1", headers=_auth(token))

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"
