"""Transactional-outbox reconciler: claim + requeue undelivered outbox rows.

The outbox is the crash-compensation fallback for task dispatch (§33.3/§34.2).
Dispatch is made idempotent by an atomic *claim* — ``UPDATE … SET delivered_at
WHERE delivered_at IS NULL`` returning rowcount 1 — so at most one dispatcher
(the immediate enqueue path or any reconciler) enqueues a given outbox row, even
though RQ itself does not deduplicate by ``job_id`` (rq>=2.0). A failed enqueue
releases the claim so the next 15s scan retries instead of silently losing the
task.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.task_outbox import TaskOutboxEvent
from app.queue import enqueue_process_task

log = structlog.get_logger(__name__)

# Match the 12s E2E assertion: a task whose enqueue was lost is re-enqueued
# within one scan interval of the crash (§34.2, Verity 方案 A).
_SCAN_INTERVAL_SECONDS = 15
_BATCH_SIZE = 100


async def claim_outbox_row(session: AsyncSession, outbox_id: str) -> bool:
    """Atomically claim an outbox row for dispatch; True if this caller won.

    ``delivered_at`` doubles as the claim marker: the conditional UPDATE only
    matches rows no one has claimed yet, so the first claimer wins and every
    other caller sees rowcount 0 and skips. Committing the claim makes it
    visible to every other dispatcher (main path and reconcilers).
    """
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(TaskOutboxEvent)
            .where(TaskOutboxEvent.id == outbox_id, TaskOutboxEvent.delivered_at.is_(None))
            .values(delivered_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        ),
    )
    return result.rowcount == 1


async def release_outbox_claim(session: AsyncSession, outbox_id: str) -> None:
    """Undo a claim so the reconciler retries an enqueue that failed."""
    await session.execute(
        update(TaskOutboxEvent)
        .where(TaskOutboxEvent.id == outbox_id)
        .values(delivered_at=None)
        .execution_options(synchronize_session=False)
    )


async def reconcile_undelivered(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Claim and re-enqueue undelivered outbox rows (idempotent, multi-worker safe).

    Each row is claimed and that claim is committed *immediately*, before the
    enqueue — a crash after the commit leaves the row marked delivered (no
    duplicate re-enqueue by another reconciler), while an enqueue failure
    releases the claim so the next 15s scan retries. ``FOR UPDATE SKIP LOCKED``
    keeps concurrent reconcilers from contending on the first row of a batch;
    the atomic ``delivered_at IS NULL`` claim is the actual idempotency gate.
    """
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(TaskOutboxEvent)
                .where(
                    TaskOutboxEvent.delivered_at.is_(None),
                    TaskOutboxEvent.created_at
                    < datetime.now(UTC) - timedelta(seconds=_SCAN_INTERVAL_SECONDS),
                )
                .order_by(TaskOutboxEvent.created_at)
                .limit(_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
            pending = result.scalars().all()
            for event in pending:
                claimed = await claim_outbox_row(session, event.id)
                # Commit the claim before enqueueing: the claim must be durable
                # so a concurrent reconciler (or the main path) cannot re-claim
                # and double-enqueue the same row.
                await session.commit()
                if not claimed:
                    continue
                try:
                    enqueue_process_task(
                        task_id=event.task_id,
                        outbox_id=event.id,
                        queue_name=event.type,
                        payload=event.payload,
                    )
                except Exception:
                    await release_outbox_claim(session, event.id)
                    await session.commit()
                    log.warning("reconcile_enqueue_failed", outbox_id=event.id, exc_info=True)
    except Exception:
        # The reconciler must never take the app down; a transient DB/Redis
        # outage is retried on the next 15s tick.
        log.warning("reconcile_scan_failed", exc_info=True)


def create_scheduler(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIOScheduler:
    """Build an ``AsyncIOScheduler`` running the reconciler every 15s."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        reconcile_undelivered,
        "interval",
        seconds=_SCAN_INTERVAL_SECONDS,
        args=[session_factory],
        id="task-outbox-reconcile",
        replace_existing=True,
    )
    return scheduler
