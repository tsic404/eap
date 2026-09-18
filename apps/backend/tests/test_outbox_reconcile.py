"""Outbox reconciler + claim tests (§33.3/§34.2 regression coverage)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.task_outbox import TaskOutboxEvent
from app.services.task_reconcile import claim_outbox_row, reconcile_undelivered


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
