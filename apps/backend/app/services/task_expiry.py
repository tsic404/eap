"""Approval-expiry scan: retire pending approvals past their deadline (C-75).

C-73 keeps an expired approval out of reach of the happy path (``pending ->
approved`` on an expired row is 422 ``TASK_EXPIRED``) but leaves the row
``pending`` forever; this scan is what ends it. Each row is retired by a
conditional UPDATE keyed on the status the scan read, so an approval that wins
the race between the read and the update keeps its ``approved`` state — the
loser matches no row and the scan moves on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.events.task import TaskStatusChangedEvent, emit_task_status_changed_best_effort
from app.models.task import Task

log = structlog.get_logger(__name__)

# C-75 fixes the period: a pending approval is retired within one 15s cycle of
# its deadline passing.
SCAN_INTERVAL_SECONDS = 15
_BATCH_SIZE = 100

# The user-visible outcome of a timeout cancellation (C-75); the reject path
# carries its reason the same way.
TIMEOUT_RESULT_REASON = "超时取消"


async def expire_overdue_approvals(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Cancel one batch of expired pending approvals; never raise.

    A transient database outage is retried on the next 15s tick, so the scan
    logs and returns rather than letting the scheduler's job explode.
    """
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(Task.id)
                .where(
                    Task.status == "pending",
                    Task.expires_at.is_not(None),
                    Task.expires_at <= datetime.now(UTC),
                )
                .order_by(Task.expires_at)
                .limit(_BATCH_SIZE)
            )
            for task_id in result.scalars().all():
                await _cancel_expired(session, task_id)
    except Exception:
        log.warning("approval_expiry_scan_failed", exc_info=True)


async def _cancel_expired(session: AsyncSession, task_id: uuid.UUID) -> None:
    """Retire one overdue approval, committing per row."""
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == "pending")
            .values(status="cancelled", result={"reason": TIMEOUT_RESULT_REASON})
            .execution_options(synchronize_session=False)
        ),
    )
    await session.commit()
    if result.rowcount == 0:
        # Someone approved (or cancelled) the task between the scan and this
        # update; the conditional match kept the row as they left it.
        return
    await emit_task_status_changed_best_effort(
        TaskStatusChangedEvent(
            task_id=str(task_id),
            from_status="pending",
            to_status="cancelled",
            metadata=TIMEOUT_RESULT_REASON,
        )
    )
