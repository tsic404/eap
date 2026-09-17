"""Transactional-outbox reconciler: requeue undelivered outbox rows (§33.3/§34.2).

The main path enqueues immediately after the transition commits; the outbox is
the crash-compensation fallback. This scanner finds outbox rows whose enqueue
never happened (or whose delivered-mark never landed) and re-enqueues them with
the same ``job_id``, so RQ's idempotency keeps a double-delivery from running a
task twice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.task_outbox import TaskOutboxEvent
from app.queue import enqueue_process_task

log = structlog.get_logger(__name__)

# Match the 12s E2E assertion: a task whose enqueue was lost is re-enqueued
# within one scan interval of the crash (§34.2, Verity 方案 A).
_SCAN_INTERVAL_SECONDS = 15
_BATCH_SIZE = 100


async def reconcile_undelivered(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Scan for undelivered outbox rows and re-enqueue each (idempotent)."""
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(TaskOutboxEvent)
                .where(
                    TaskOutboxEvent.delivered_at.is_(None),
                    TaskOutboxEvent.created_at
                    < datetime.now(UTC) - timedelta(seconds=_SCAN_INTERVAL_SECONDS),
                )
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
                    log.warning("reconcile_enqueue_failed", outbox_id=event.id, exc_info=True)
                    continue
                await session.execute(
                    update(TaskOutboxEvent)
                    .where(TaskOutboxEvent.id == event.id)
                    .values(delivered_at=datetime.now(UTC))
                    .execution_options(synchronize_session=False)
                )
            if pending:
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
