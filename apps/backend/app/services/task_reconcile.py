"""Transactional-outbox reconciler: requeue undelivered outbox rows.

The outbox is the crash-compensation fallback for task dispatch (§33.3/§34.2).
The reconciler follows the same at-least-once order as the main path: enqueue
first, and only after a *successful* enqueue atomically claim the row
(``UPDATE … SET delivered_at WHERE delivered_at IS NULL``). A failed enqueue
leaves the row undelivered so the next 15s scan retries — no permanent-loss
window. A duplicate job (a crash between enqueue and commit, or two concurrent
reconcilers) is absorbed by the worker's execution lease.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from redis import Redis
from sqlalchemy import CursorResult, Select, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.config import get_settings
from app.models.task import Task
from app.models.task_outbox import TaskOutboxEvent
from app.models.tool import ToolRegistry
from app.queue import enqueue_process_task
from app.services.task_expiry import SCAN_INTERVAL_SECONDS, expire_overdue_approvals
from app.services.task_lease import LEASE_TTL_SECONDS

log = structlog.get_logger(__name__)

# Match the 12s E2E assertion: a task whose enqueue was lost is re-enqueued
# within one scan interval of the crash (§34.2, Verity 方案 A).
_SCAN_INTERVAL_SECONDS = 15
_BATCH_SIZE = 100

# No dispatch has a grace below its own lease: even a task the worker fails on
# sight keeps its lease for that long. The exact per-dispatch grace is applied
# after the query, from the tool's declared execution budget.
_MIN_RESCUE_GRACE_SECONDS = LEASE_TTL_SECONDS
_RESCUE_MARKER_KEY = "task:rescue:{outbox_id}"
# The state machine's pre-terminal states (C-73); an outbox dispatch is only
# worth rescuing while its task still sits in one of them.
_RESCUABLE_STATUSES = frozenset({"approved", "executing"})
# Eligibility is decided per dispatch (the grace comes from its tool), so the
# scan pages through candidates until it has re-sent a full batch. Stopping at
# one page would let not-yet-eligible rows at the head of the age order starve
# younger stuck dispatches; _SCAN_MAX_PAGES bounds one tick's work.
_SCAN_PAGE_SIZE = 100
_SCAN_MAX_PAGES = 10


def _execution_budget_seconds(timeout_ms: int | None, retry_policy: Any) -> int:
    """Worst-case wall clock of one execution, from the tool's own declaration.

    Every attempt may burn its full timeout and every retry waits out its
    backoff, so the budget is the sum of both. A policy that cannot be read
    falls back to the same defaults the proxy uses.
    """
    if not timeout_ms or timeout_ms <= 0:
        return 0
    policy: dict[str, Any] = retry_policy if isinstance(retry_policy, dict) else {}
    try:
        retries = max(0, int(policy.get("max_retries", 2)))
    except (TypeError, ValueError):
        retries = 2
    try:
        base_delay_ms = max(0, int(policy.get("base_delay_ms", 1000)))
    except (TypeError, ValueError):
        base_delay_ms = 1000
    backoff_ms = sum(base_delay_ms << attempt for attempt in range(retries))
    return math.ceil(timeout_ms / 1000) * (retries + 1) + math.ceil(backoff_ms / 1000)


def _rescue_grace_seconds(timeout_ms: int | None, retry_policy: Any) -> int:
    """How long a delivered dispatch may look stuck before it is re-sent.

    Re-sending while the holder is alive cannot run the tool twice — the
    duplicate fails to take the lease — so the grace exists for the dead
    holder: the crash can happen as late as the end of the execution budget,
    and the lease it stopped renewing then survives its own TTL.
    """
    return _execution_budget_seconds(timeout_ms, retry_policy) + LEASE_TTL_SECONDS


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
    worker's execution lease.
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

    RQ gives up on a job whose worker died once the retry budget is spent, and
    never requeues one it lost before registering; this scan is the last net.
    The grace is the tool's own execution budget plus the lease, so a live
    execution is never re-sent, and the per-dispatch marker bounds resends to
    one per grace period. Runnable status is filtered in SQL, not after the
    limit: the outbox is append-only, so a batch picked purely by age would
    return the oldest terminal history forever. Eligibility still needs the
    grace, which is per dispatch, so the scan pages until it has re-sent a
    full batch instead of cutting the age order at one page.
    """
    client = marker_redis or _get_rescue_redis()
    now = datetime.now(UTC)
    try:
        async with session_factory() as session:
            query = _rescue_candidates(now)
            rescued = 0
            offset = 0
            for _ in range(_SCAN_MAX_PAGES):
                result = await session.execute(query.offset(offset).limit(_SCAN_PAGE_SIZE))
                rows = result.all()
                if not rows:
                    break
                for event, timeout_ms, retry_policy in rows:
                    if rescued >= _BATCH_SIZE:
                        break
                    if await _rescue_dispatch(
                        session, client, event, timeout_ms, retry_policy, now
                    ):
                        rescued += 1
                offset += len(rows)
                if rescued >= _BATCH_SIZE or len(rows) < _SCAN_PAGE_SIZE:
                    break
    except Exception:
        # Like the reconciler, the scan must never take the app down.
        log.warning("rescue_scan_failed", exc_info=True)


def _rescue_candidates(now: datetime) -> Select[Any]:
    """Delivered, still-runnable, not-superseded dispatches, oldest first."""
    newer = aliased(TaskOutboxEvent)
    newer_dispatch = (
        select(newer.id)
        .where(
            newer.task_id == TaskOutboxEvent.task_id,
            newer.created_at > TaskOutboxEvent.created_at,
        )
        .exists()
    )
    return (
        select(
            TaskOutboxEvent,
            ToolRegistry.timeout_ms,
            ToolRegistry.retry_policy,
        )
        .join(Task, Task.id == sql_cast(TaskOutboxEvent.task_id, UUID(as_uuid=True)))
        # Left join: a dispatch whose tool was removed still has to be rescued,
        # and it only carries the smallest grace.
        .outerjoin(
            ToolRegistry,
            ToolRegistry.tool_id == TaskOutboxEvent.payload["tool_id"].astext,
        )
        .where(
            TaskOutboxEvent.delivered_at.is_not(None),
            TaskOutboxEvent.delivered_at < now - timedelta(seconds=_MIN_RESCUE_GRACE_SECONDS),
            Task.status.in_(_RESCUABLE_STATUSES),
            ~newer_dispatch,
        )
        .order_by(TaskOutboxEvent.created_at)
    )


async def _rescue_dispatch(
    session: AsyncSession,
    client: Redis,
    event: TaskOutboxEvent,
    timeout_ms: int | None,
    retry_policy: Any,
    now: datetime,
) -> bool:
    """Re-send one dispatch when its own grace has passed; True if sent."""
    grace = _rescue_grace_seconds(timeout_ms, retry_policy)
    delivered_at = event.delivered_at
    if delivered_at is None or delivered_at > now - timedelta(seconds=grace):
        # Inside its own execution budget: the holder may still be running.
        return False
    task = await session.get(Task, uuid.UUID(event.task_id))
    if task is None or task.status not in _RESCUABLE_STATUSES:
        return False
    marker = _RESCUE_MARKER_KEY.format(outbox_id=event.id)
    if not client.set(marker, "1", nx=True, ex=grace):
        return False
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
        return False
    log.warning(
        "task_dispatch_rescued",
        outbox_id=event.id,
        task_id=event.task_id,
        status=task.status,
    )
    return True


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
