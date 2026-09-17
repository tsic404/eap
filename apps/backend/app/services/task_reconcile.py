"""Transactional-outbox reconciler: requeue undelivered outbox rows.

The outbox is the crash-compensation fallback for task dispatch (§33.3/§34.2).
The reconciler follows the same at-least-once order as the main path: enqueue
first, and only after a *successful* enqueue atomically claim the row
(``UPDATE … SET delivered_at WHERE delivered_at IS NULL``). A failed enqueue
leaves the row undelivered so the next 15s scan retries — no permanent-loss
window. A duplicate job (a crash between enqueue and commit, or two concurrent
reconcilers) is absorbed by the worker's ``SETNX`` execution claim.
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
    """Atomically mark an outbox row delivered; True if this caller won.

    The conditional UPDATE only matches rows still undelivered, so the first
    claimer marks it and every other caller sees rowcount 0. Committing the
    claim stops the next scan (and any other reconciler) from re-enqueueing it.
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


async def reconcile_undelivered(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Re-enqueue undelivered outbox rows (at-least-once, same order as main path).

    Each row is enqueued first; only a successful enqueue is followed by the
    atomic claim + commit. A failed enqueue leaves the row undelivered (no
    claim, no loss). Duplicates from a crash between enqueue and commit — or
    two concurrent reconcilers scanning the same row — are absorbed by the
    worker's ``SETNX`` execution claim.
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
            )
            pending = result.scalars().all()
            for event in pending:
                try:
                    enqueue_process_task(
                        task_id=event.task_id,
                        outbox_id=event.id,
                        queue_name=event.type,
                        payload=event.payload,
                    )
                except Exception:
                    # Enqueue failed: stay undelivered so the next scan retries.
                    log.warning("reconcile_enqueue_failed", outbox_id=event.id, exc_info=True)
                    continue
                await claim_outbox_row(session, event.id)
                await session.commit()
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
