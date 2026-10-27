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

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from redis import Redis
from sqlalchemy import CursorResult, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.config import get_settings
from app.models.task import Task
from app.models.task_outbox import TaskOutboxEvent
from app.queue import enqueue_process_task
from app.services.task_expiry import SCAN_INTERVAL_SECONDS, expire_overdue_approvals

log = structlog.get_logger(__name__)

# Match the 12s E2E assertion: a task whose enqueue was lost is re-enqueued
# within one scan interval of the crash (§34.2, Verity 方案 A).
_SCAN_INTERVAL_SECONDS = 15
_BATCH_SIZE = 100

# A dispatch still holding 已批准/执行中 after this long lost its worker for
# good: RQ gives up once the retry budget is spent. A fixed window, not derived
# from the tool's timeout (which has no upper bound), so a tool that allows a
# call longer than this can see one duplicate call; the conditional status
# writes keep the task row itself consistent.
_RESCUE_GRACE_SECONDS = 900
_RESCUE_MARKER_KEY = "task:rescue:{outbox_id}"
# The state machine's pre-terminal states (C-73); an outbox dispatch is only
# worth rescuing while its task still sits in one of them.
_RESCUABLE_STATUSES = frozenset({"approved", "executing"})

_rescue_redis: Redis | None = None


def _get_rescue_redis() -> Redis:
    """Return the process-wide Redis client backing the rescue markers."""
    global _rescue_redis
    if _rescue_redis is None:
        _rescue_redis = Redis.from_url(get_settings().redis_url)
    return _rescue_redis


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


async def rescue_stuck_dispatches(
    session_factory: async_sessionmaker[AsyncSession],
    marker_redis: Redis | None = None,
) -> None:
    """Re-enqueue dispatches whose task never left 已批准/执行中.

    RQ requeues a job whose worker died while it held the job, but it gives up
    once the retry budget is spent, and a job lost before RQ registered it is
    never requeued at all. This scan is the last net: a delivered dispatch older
    than the grace period whose task is still runnable goes back on the queue,
    where the worker takes the stale execution claim over and finishes it. The
    per-dispatch marker bounds resends to one per grace period, so a slow call
    is not repeated on every 15s tick.

    Runnable status is filtered in SQL, not after the LIMIT: the outbox is
    append-only, so every finished approval leaves a delivered row behind, and
    a batch picked purely by age would keep returning the oldest terminal
    history while a newly stuck dispatch never entered it.
    """
    client = marker_redis or _get_rescue_redis()
    try:
        async with session_factory() as session:
            newer = aliased(TaskOutboxEvent)
            newer_dispatch = (
                select(newer.id)
                .where(
                    newer.task_id == TaskOutboxEvent.task_id,
                    newer.created_at > TaskOutboxEvent.created_at,
                )
                .exists()
            )
            result = await session.execute(
                select(TaskOutboxEvent)
                .join(Task, Task.id == sql_cast(TaskOutboxEvent.task_id, UUID(as_uuid=True)))
                .where(
                    TaskOutboxEvent.delivered_at.is_not(None),
                    TaskOutboxEvent.delivered_at
                    < datetime.now(UTC) - timedelta(seconds=_RESCUE_GRACE_SECONDS),
                    Task.status.in_(_RESCUABLE_STATUSES),
                    ~newer_dispatch,
                )
                .order_by(TaskOutboxEvent.created_at)
                .limit(_BATCH_SIZE)
            )
            for event in result.scalars().all():
                task = await session.get(Task, uuid.UUID(event.task_id))
                if task is None or task.status not in _RESCUABLE_STATUSES:
                    continue
                marker = _RESCUE_MARKER_KEY.format(outbox_id=event.id)
                if not client.set(marker, "1", nx=True, ex=_RESCUE_GRACE_SECONDS):
                    continue
                try:
                    enqueue_process_task(
                        task_id=event.task_id,
                        outbox_id=event.id,
                        queue_name=event.type,
                        payload=event.payload,
                    )
                except Exception:
                    # Release the marker so the next tick retries the rescue.
                    client.delete(marker)
                    log.warning("rescue_enqueue_failed", outbox_id=event.id, exc_info=True)
                    continue
                log.warning(
                    "task_dispatch_rescued",
                    outbox_id=event.id,
                    task_id=event.task_id,
                    status=task.status,
                )
    except Exception:
        # Like the reconciler, the scan must never take the app down.
        log.warning("rescue_scan_failed", exc_info=True)


def create_scheduler(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIOScheduler:
    """Build the ``AsyncIOScheduler`` owning the task background scans.

    Three 15s jobs, each fixing one stuck state with no request in flight: the
    outbox reconciler re-dispatches undelivered rows, the approval-expiry scan
    retires pending approvals nobody acted on, and the rescue scan re-sends
    dispatches whose task never reached a terminal state.
    """
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        reconcile_undelivered,
        "interval",
        seconds=_SCAN_INTERVAL_SECONDS,
        args=[session_factory],
        id="task-outbox-reconcile",
        replace_existing=True,
    )
    scheduler.add_job(
        expire_overdue_approvals,
        "interval",
        seconds=SCAN_INTERVAL_SECONDS,
        args=[session_factory],
        id="task-approval-expiry",
        replace_existing=True,
    )
    scheduler.add_job(
        rescue_stuck_dispatches,
        "interval",
        seconds=_SCAN_INTERVAL_SECONDS,
        args=[session_factory],
        id="task-dispatch-rescue",
        replace_existing=True,
    )
    return scheduler
