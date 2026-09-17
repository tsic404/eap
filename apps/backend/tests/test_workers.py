"""RQ audit worker tests: payload mapping and the queue->DB write path."""

import uuid
from datetime import UTC, datetime
from typing import Any

from app.models.audit_log import AuditLog
from app.workers.audit import _audit_log_from_payload, write_audit_log


def test_audit_log_from_payload_maps_fields() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    occurred_at = datetime(2026, 9, 12, 1, 2, 3, tzinfo=UTC)
    payload = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "action": "agent.create",
        "resource": "agent",
        "resource_id": "agent-1",
        "details": {"name": "support-bot"},
        "ip_address": "1.2.3.4",
        "user_agent": "pytest",
        "occurred_at": occurred_at,
    }
    row = _audit_log_from_payload(payload)
    assert row.tenant_id == tenant_id
    assert row.user_id == user_id
    assert row.action == "agent.create"
    assert row.resource == "agent"
    assert row.resource_id == "agent-1"
    assert row.details == {"name": "support-bot"}
    assert row.ip_address == "1.2.3.4"
    assert row.user_agent == "pytest"
    assert row.created_at == occurred_at


def test_audit_log_from_payload_parses_string_uuids() -> None:
    tenant_id = uuid.uuid4()
    row = _audit_log_from_payload(
        {
            "tenant_id": str(tenant_id),
            "user_id": None,
            "action": "agent.create",
            "resource": "agent",
        }
    )
    assert row.tenant_id == tenant_id
    assert row.user_id is None


def test_audit_log_from_payload_defaults_created_at() -> None:
    row = _audit_log_from_payload(
        {
            "tenant_id": uuid.uuid4(),
            "user_id": None,
            "action": "agent.create",
            "resource": "agent",
        }
    )
    assert row.created_at is not None


def test_write_audit_log_commits_row(monkeypatch: Any) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.added: list[AuditLog] = []
            self.committed = False

        def add(self, obj: AuditLog) -> None:
            self.added.append(obj)

        async def commit(self) -> None:
            self.committed = True

        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    session = FakeSession()

    def fake_factory() -> FakeSession:
        return session

    monkeypatch.setattr("app.workers.audit.async_session_factory", fake_factory)

    tenant_id = uuid.uuid4()
    write_audit_log(
        {
            "tenant_id": tenant_id,
            "user_id": None,
            "action": "agent.create",
            "resource": "agent",
        }
    )

    assert session.committed is True
    assert len(session.added) == 1
    assert session.added[0].tenant_id == tenant_id
    assert session.added[0].action == "agent.create"
