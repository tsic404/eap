"""EventBus, audit event, and RQ handoff tests."""

import uuid
from dataclasses import asdict
from typing import Any

import pytest

from app.events.audit import (
    AUDIT_LOG_EVENT,
    AuditLogEvent,
    emit_audit_log,
    register_audit_log_handler,
)
from app.events.bus import EventBus
from app.queue import AUDIT_LOG_JOB, enqueue_audit_log


@pytest.mark.asyncio
async def test_bus_dispatches_payload_to_subscribers() -> None:
    bus = EventBus()
    received: list[tuple[str, dict[str, Any]]] = []

    async def handler(sender: str, **payload: Any) -> None:
        received.append((sender, payload))

    bus.subscribe("thing.done", handler)
    await bus.emit("thing.done", id=1)
    assert received == [("thing.done", {"id": 1})]


@pytest.mark.asyncio
async def test_bus_awaits_handlers_in_registration_order() -> None:
    bus = EventBus()
    order: list[str] = []

    async def first(sender: str, **payload: Any) -> None:
        order.append("first")

    async def second(sender: str, **payload: Any) -> None:
        order.append("second")

    bus.subscribe("evt", first)
    bus.subscribe("evt", second)
    await bus.emit("evt")
    assert order == ["first", "second"]


@pytest.mark.asyncio
async def test_bus_unsubscribe_stops_dispatch() -> None:
    bus = EventBus()
    received: list[Any] = []

    async def handler(sender: str, **payload: Any) -> None:
        received.append(payload)

    bus.subscribe("evt", handler)
    bus.unsubscribe("evt", handler)
    await bus.emit("evt", x=1)
    assert received == []


@pytest.mark.asyncio
async def test_bus_emit_without_subscribers_is_noop() -> None:
    await EventBus().emit("nothing", x=1)


def test_audit_log_event_serializes_to_table_shape() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    event = AuditLogEvent(
        tenant_id=tenant_id,
        action="agent.create",
        resource="agent",
        user_id=user_id,
    )
    payload = asdict(event)
    assert payload["tenant_id"] == tenant_id
    assert payload["user_id"] == user_id
    assert payload["action"] == "agent.create"
    assert payload["resource"] == "agent"
    assert payload["occurred_at"] is not None


@pytest.mark.asyncio
async def test_emit_audit_log_enqueues_through_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_enqueue(payload: dict[str, Any], queue: Any = None) -> None:
        captured.update(payload)

    monkeypatch.setattr("app.events.audit.enqueue_audit_log", fake_enqueue)
    register_audit_log_handler()

    event = AuditLogEvent(
        tenant_id=uuid.uuid4(),
        action="agent.create",
        resource="agent",
    )
    await emit_audit_log(event)
    assert captured["action"] == "agent.create"
    assert captured["resource"] == "agent"


@pytest.mark.asyncio
async def test_emit_audit_log_swallows_enqueue_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Audit persistence is best-effort: a Redis/queue failure must not raise
    # out of emit_audit_log and fail the business request.
    def failing_enqueue(payload: dict[str, Any], queue: Any = None) -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.events.audit.enqueue_audit_log", failing_enqueue)
    register_audit_log_handler()

    event = AuditLogEvent(
        tenant_id=uuid.uuid4(),
        action="agent.create",
        resource="agent",
    )
    await emit_audit_log(event)  # must not raise


def test_enqueue_audit_log_uses_target_job_and_payload() -> None:
    class FakeQueue:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def enqueue(self, job: str, payload: dict[str, Any]) -> None:
            self.calls.append((job, payload))

    queue = FakeQueue()
    enqueue_audit_log({"action": "agent.create"}, queue=queue)  # type: ignore[arg-type]
    assert queue.calls == [(AUDIT_LOG_JOB, {"action": "agent.create"})]


def test_audit_log_event_name_is_stable() -> None:
    assert AUDIT_LOG_EVENT == "audit_log"
