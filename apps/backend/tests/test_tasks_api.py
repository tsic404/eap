"""TaskController HTTP tests (stubbed session + signed JWT)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models.task import Task
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


def _make_user(tenant: Tenant, role: str = "agent_admin") -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        name="Test User",
        role=role,
        status="active",
    )


def _make_task(**overrides: object) -> Task:
    now = datetime.now(UTC)
    fields: dict[str, object] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "creator_id": uuid.uuid4(),
        "assignee_id": None,
        "type": "tool_approval",
        "title": "Approve tool",
        "priority": "high",
        "status": "pending",
        "payload": {"tool_id": "my-tool", "params": {"a": 1}},
        "result": None,
        "error_message": None,
        "retry_count": 0,
        "max_retries": 3,
        "expires_at": None,
        "created_at": now,
        "resolved_at": None,
    }
    fields.update(overrides)
    return Task(**fields)  # type: ignore[arg-type]


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


class _FakeBegin:
    """Async context-manager stand-in for ``AsyncSession.begin``."""

    def __init__(self, session: AsyncMock) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if exc_type is None:
            await self.session.commit()
        else:
            await self.session.rollback()
        return False


def _make_session(
    user: User,
    tenant: Tenant,
    *,
    select_task: Task | None = None,
    final_task: Task | None = None,
    update_rowcount: int = 1,
) -> AsyncMock:
    session = AsyncMock()
    session.add = Mock()
    session.begin = Mock(return_value=_FakeBegin(session))

    async def _get(model: object, pk: object, **kwargs: object) -> object | None:
        if model is User:
            return user
        if model is Tenant:
            return tenant
        if model is Task:
            return final_task
        return None

    session.get = AsyncMock(side_effect=_get)

    select_result = AsyncMock()
    select_result.scalar_one_or_none = Mock(return_value=select_task)
    update_result = Mock()
    update_result.rowcount = update_rowcount
    # Third ``execute`` call is the outbox claim (``claim_outbox_row``); a
    # rowcount of 1 means the caller won the claim and proceeds to enqueue.
    claim_result = Mock()
    claim_result.rowcount = 1
    session.execute = AsyncMock(side_effect=[select_result, update_result, claim_result])
    return session


def _make_client(
    user: User, tenant: Tenant, session: AsyncMock, monkeypatch
) -> tuple[TestClient, str, list]:
    captured: list[dict[str, object]] = []

    def fake_enqueue(*, task_id: str, outbox_id: str, queue_name: str, payload: dict) -> None:
        captured.append({"task_id": task_id, "outbox_id": outbox_id, "queue_name": queue_name})

    monkeypatch.setattr("app.services.task_service.enqueue_process_task", fake_enqueue)

    private_key, public_pem = _keypair()
    token = _signed_token(private_key, user, tenant)

    async def _override_session() -> AsyncMock:
        return session

    app: FastAPI = create_app(
        Settings(_env_file=None, jwt_public_key=public_pem, rate_limit_enabled=False)
    )
    app.dependency_overrides[get_session] = _override_session
    return TestClient(app, raise_server_exceptions=False), token, captured


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_approve_returns_200_approved_and_enqueues(monkeypatch):
    tenant = _make_tenant()
    user = _make_user(tenant)
    pending = _make_task(tenant_id=tenant.id, creator_id=user.id, status="pending")
    approved = _make_task(tenant_id=tenant.id, creator_id=user.id, status="approved")
    session = _make_session(user, tenant, select_task=pending, final_task=approved)

    client, token, captured = _make_client(user, tenant, session, monkeypatch)
    resp = client.post(f"/api/tasks/{pending.id}/approve", json={}, headers=_auth(token))

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "approved"
    assert captured and captured[0]["task_id"] == str(pending.id)


def test_approve_requires_agent_admin(monkeypatch):
    tenant = _make_tenant()
    user = _make_user(tenant, role="employee")
    session = _make_session(user, tenant, select_task=None, final_task=None)

    client, token, _ = _make_client(user, tenant, session, monkeypatch)
    resp = client.post(f"/api/tasks/{uuid.uuid4()}/approve", json={}, headers=_auth(token))

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_approve_invalid_transition_returns_422(monkeypatch):
    tenant = _make_tenant()
    user = _make_user(tenant)
    completed = _make_task(tenant_id=tenant.id, creator_id=user.id, status="completed")
    session = _make_session(user, tenant, select_task=completed, final_task=completed)

    client, token, _ = _make_client(user, tenant, session, monkeypatch)
    resp = client.post(f"/api/tasks/{completed.id}/approve", json={}, headers=_auth(token))

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TRANSITION"


def test_approve_expired_returns_422(monkeypatch):
    tenant = _make_tenant()
    user = _make_user(tenant)
    expired = _make_task(
        tenant_id=tenant.id,
        creator_id=user.id,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    session = _make_session(user, tenant, select_task=expired, final_task=expired)

    client, token, _ = _make_client(user, tenant, session, monkeypatch)
    resp = client.post(f"/api/tasks/{expired.id}/approve", json={}, headers=_auth(token))

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "TASK_EXPIRED"


def test_get_unknown_task_returns_404(monkeypatch):
    tenant = _make_tenant()
    user = _make_user(tenant)
    session = _make_session(user, tenant, select_task=None, final_task=None)

    client, token, _ = _make_client(user, tenant, session, monkeypatch)
    resp = client.get(f"/api/tasks/{uuid.uuid4()}", headers=_auth(token))

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"
