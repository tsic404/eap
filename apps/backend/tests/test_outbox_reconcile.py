"""Outbox reconciler + claim tests (§33.3/§34.2 regression coverage)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.task import Task
from app.models.task_outbox import TaskOutboxEvent
from app.services.task_reconcile import (
    claim_outbox_row,
    reconcile_undelivered,
    rescue_stuck_dispatches,
)
from tests.conftest import seed_task, seed_tenant_user


async def _seed_outbox(
    session_factory,
    *,
    delivered_at: datetime | None = None,
    created_at: datetime | None = None,
) -> str:
    event = TaskOutboxEvent(
        id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()),
        type="tool-approval",
        status="approved",
        payload={"tool_id": "my-tool"},
        created_at=created_at or datetime.now(UTC) - timedelta(seconds=30),
        delivered_at=delivered_at,
    )
    async with session_factory() as session:
        session.add(event)
        await session.commit()
        return event.id


@pytest.mark.asyncio
async def test_claim_outbox_row_is_idempotent(session_factory):
    outbox_id = await _seed_outbox(session_factory)

    async with session_factory() as session:
        assert await claim_outbox_row(session, outbox_id) is True
        await session.commit()

    async with session_factory() as session:
        assert await claim_outbox_row(session, outbox_id) is False


@pytest.mark.asyncio
async def test_reconcile_enqueues_undelivered_and_marks_delivered(session_factory, monkeypatch):
    outbox_id = await _seed_outbox(session_factory)
    captured: list[dict[str, object]] = []

    def fake_enqueue(*, task_id: str, outbox_id: str, queue_name: str, payload: dict) -> None:
        captured.append({"task_id": task_id, "outbox_id": outbox_id, "queue_name": queue_name})

    monkeypatch.setattr("app.services.task_reconcile.enqueue_process_task", fake_enqueue)

    await reconcile_undelivered(session_factory)

    assert len(captured) == 1
    assert captured[0]["outbox_id"] == outbox_id
    assert captured[0]["queue_name"] == "tool-approval"

    async with session_factory() as session:
        event = await session.get(TaskOutboxEvent, outbox_id)
        assert event.delivered_at is not None


@pytest.mark.asyncio
async def test_reconcile_skips_delivered_and_recent(session_factory, monkeypatch):
    # Already delivered, and a row too recent to be eligible (within 15s).
    await _seed_outbox(session_factory, delivered_at=datetime.now(UTC))
    await _seed_outbox(session_factory, created_at=datetime.now(UTC))
    captured: list[dict[str, object]] = []

    def fake_enqueue(*, task_id: str, outbox_id: str, queue_name: str, payload: dict) -> None:
        captured.append({"outbox_id": outbox_id})

    monkeypatch.setattr("app.services.task_reconcile.enqueue_process_task", fake_enqueue)

    await reconcile_undelivered(session_factory)

    assert captured == []


@pytest.mark.asyncio
async def test_reconcile_releases_claim_on_enqueue_failure(session_factory, monkeypatch):
    outbox_id = await _seed_outbox(session_factory)

    def failing_enqueue(*, task_id: str, outbox_id: str, queue_name: str, payload: dict) -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.services.task_reconcile.enqueue_process_task", failing_enqueue)

    await reconcile_undelivered(session_factory)

    # Enqueue failed, so the claim must be released: the row stays undelivered
    # for the next scan to retry.
    async with session_factory() as session:
        event = await session.get(TaskOutboxEvent, outbox_id)
        assert event.delivered_at is None


@pytest.mark.asyncio
async def test_reconcile_per_row_commit_survives_later_failure(session_factory, monkeypatch):
    """Per-row commit: a later enqueue failure must not roll back an earlier
    row's successful claim (first delivered, second released for retry)."""
    older = datetime.now(UTC) - timedelta(seconds=60)
    newer = datetime.now(UTC) - timedelta(seconds=30)
    first_id = await _seed_outbox(session_factory, created_at=older)
    second_id = await _seed_outbox(session_factory, created_at=newer)
    captured: list[dict[str, object]] = []

    def fake_enqueue(*, task_id: str, outbox_id: str, queue_name: str, payload: dict) -> None:
        if outbox_id == second_id:
            raise RuntimeError("redis down")
        captured.append({"outbox_id": outbox_id})

    monkeypatch.setattr("app.services.task_reconcile.enqueue_process_task", fake_enqueue)

    await reconcile_undelivered(session_factory)

    assert [c["outbox_id"] for c in captured] == [first_id]
    async with session_factory() as session:
        first = await session.get(TaskOutboxEvent, first_id)
        second = await session.get(TaskOutboxEvent, second_id)
        assert first.delivered_at is not None
        assert second.delivered_at is None


class _MarkerRedis:
    """Sync stand-in for the marker commands the rescue scan issues."""

    def __init__(self) -> None:
        self.ttls: dict[str, int | None] = {}

    def set(self, name: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and name in self.ttls:
            return None
        self.ttls[name] = ex
        return True

    def delete(self, name: str) -> None:
        self.ttls.pop(name, None)


async def _seed_dispatch(
    session_factory,
    *,
    task_status: str,
    delivered_age_s: int | None,
) -> tuple[uuid.UUID, str]:
    async with session_factory() as session:
        tenant, user = await seed_tenant_user(session, worker_index="rescue")
        task = await seed_task(session, tenant, user, status=task_status)
        event = TaskOutboxEvent(
            id=str(uuid.uuid4()),
            task_id=str(task.id),
            type="tool-approval",
            status="approved",
            payload={"tool_id": "tool-1"},
            created_at=datetime.now(UTC) - timedelta(seconds=delivered_age_s or 0),
            delivered_at=(
                datetime.now(UTC) - timedelta(seconds=delivered_age_s)
                if delivered_age_s is not None
                else None
            ),
        )
        session.add(event)
        await session.commit()
        return task.id, event.id


@pytest.mark.asyncio
async def test_rescue_reenqueues_dispatch_whose_task_never_finished(session_factory, monkeypatch):
    """A task stuck in 执行中 after its worker died is dispatched again."""
    task_id, outbox_id = await _seed_dispatch(
        session_factory, task_status="executing", delivered_age_s=1800
    )
    enqueued: list[tuple[str, str]] = []

    def fake_enqueue(*, task_id: str, outbox_id: str, queue_name: str, payload: dict) -> None:
        enqueued.append((task_id, outbox_id))

    monkeypatch.setattr("app.services.task_reconcile.enqueue_process_task", fake_enqueue)
    marker = _MarkerRedis()

    await rescue_stuck_dispatches(session_factory, marker_redis=marker)
    assert enqueued == [(str(task_id), outbox_id)]

    # The marker caps the 15s tick: the same dispatch is not sent again.
    await rescue_stuck_dispatches(session_factory, marker_redis=marker)
    assert enqueued == [(str(task_id), outbox_id)]


@pytest.mark.asyncio
async def test_rescue_skips_finished_undelivered_and_recent_dispatches(
    session_factory, monkeypatch
):
    await _seed_dispatch(session_factory, task_status="completed", delivered_age_s=1800)
    await _seed_dispatch(session_factory, task_status="approved", delivered_age_s=None)
    await _seed_dispatch(session_factory, task_status="approved", delivered_age_s=60)
    enqueued: list[str] = []

    def fake_enqueue(*, task_id: str, outbox_id: str, queue_name: str, payload: dict) -> None:
        enqueued.append(outbox_id)

    monkeypatch.setattr("app.services.task_reconcile.enqueue_process_task", fake_enqueue)

    await rescue_stuck_dispatches(session_factory, marker_redis=_MarkerRedis())
    assert enqueued == []


@pytest.mark.asyncio
async def test_rescue_ignores_superseded_dispatch(session_factory, monkeypatch):
    """Only a task's newest dispatch row may be re-sent."""
    async with session_factory() as session:
        tenant, user = await seed_tenant_user(session, worker_index="rescue")
        task = await seed_task(session, tenant, user, status="approved")
        stale = TaskOutboxEvent(
            id=str(uuid.uuid4()),
            task_id=str(task.id),
            type="tool-approval",
            status="approved",
            payload={"tool_id": "tool-1"},
            created_at=datetime.now(UTC) - timedelta(hours=1),
            delivered_at=datetime.now(UTC) - timedelta(hours=1),
        )
        fresh = TaskOutboxEvent(
            id=str(uuid.uuid4()),
            task_id=str(task.id),
            type="tool-approval",
            status="approved",
            payload={"tool_id": "tool-1"},
            created_at=datetime.now(UTC) - timedelta(seconds=30),
            delivered_at=datetime.now(UTC) - timedelta(seconds=30),
        )
        session.add_all([stale, fresh])
        await session.commit()
    enqueued: list[str] = []

    def fake_enqueue(*, task_id: str, outbox_id: str, queue_name: str, payload: dict) -> None:
        enqueued.append(outbox_id)

    monkeypatch.setattr("app.services.task_reconcile.enqueue_process_task", fake_enqueue)

    await rescue_stuck_dispatches(session_factory, marker_redis=_MarkerRedis())
    assert enqueued == []


@pytest.mark.asyncio
async def test_rescue_reaches_a_stuck_dispatch_behind_a_hundred_terminal_ones(
    session_factory, monkeypatch
):
    """Terminal history must not fill the batch and starve the one stuck dispatch.

    The outbox has no retention cleanup, so the scan's age ordering alone hands
    back the oldest finished approvals forever; the runnable status has to be
    part of the query, or a dispatch that gets stuck today is never reached.
    """
    now = datetime.now(UTC)
    async with session_factory() as session:
        tenant, user = await seed_tenant_user(session, worker_index="rescue-bulk")
        finished = [
            Task(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                creator_id=user.id,
                type="tool_approval",
                title="Finished approval",
                priority="high",
                status="completed",
                payload={"tool_id": "tool-1"},
            )
            for _ in range(150)
        ]
        session.add_all(finished)
        session.add_all(
            TaskOutboxEvent(
                id=str(uuid.uuid4()),
                task_id=str(task.id),
                type="tool-approval",
                status="approved",
                payload={"tool_id": "tool-1"},
                created_at=now - timedelta(hours=3) + timedelta(seconds=index),
                delivered_at=now - timedelta(hours=3) + timedelta(seconds=index),
            )
            for index, task in enumerate(finished)
        )
        stuck = Task(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            creator_id=user.id,
            type="tool_approval",
            title="Stuck approval",
            priority="high",
            status="executing",
            payload={"tool_id": "tool-1"},
        )
        session.add(stuck)
        await session.flush()
        stuck_outbox_id = str(uuid.uuid4())
        session.add(
            TaskOutboxEvent(
                id=stuck_outbox_id,
                task_id=str(stuck.id),
                type="tool-approval",
                status="approved",
                payload={"tool_id": "tool-1"},
                created_at=now - timedelta(hours=1),
                delivered_at=now - timedelta(hours=1),
            )
        )
        await session.commit()
        stuck_task_id = str(stuck.id)

    enqueued: list[tuple[str, str]] = []

    def fake_enqueue(*, task_id: str, outbox_id: str, queue_name: str, payload: dict) -> None:
        enqueued.append((task_id, outbox_id))

    monkeypatch.setattr("app.services.task_reconcile.enqueue_process_task", fake_enqueue)

    await rescue_stuck_dispatches(session_factory, marker_redis=_MarkerRedis())

    assert enqueued == [(stuck_task_id, stuck_outbox_id)]
