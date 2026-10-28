"""RQ worker job: execute an approved task (ApprovalConsumer).

RQ requeues a job whose worker died, and the reconciler re-sends undelivered
dispatch records. This worker takes the dispatch's execution lease — renewed
for the length of the job — then re-checks the status, so a duplicate cannot
run concurrently and a cancelled task never runs. Every status write is a
conditional UPDATE, so a cancel landing mid-flight is never overwritten.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_factory
from app.events.task import TaskStatusChangedEvent, emit_task_status_changed_best_effort
from app.models.task import Task
from app.models.tool import ToolRegistry
from app.services.task_lease import ExecutionLease
from app.services.tool_proxy import ExecutionResult, ToolProxy
from app.services.tool_service import ToolService
from app.workers.bridge import get_runtime_loop

log = structlog.get_logger(__name__)

# Statuses in which a task may still run. A cancelled/completed/failed/rejected
# task (status changed after enqueue) must be skipped, not executed.
_RUNNABLE_STATUSES = frozenset({"approved", "executing"})
# Only a running task may be finished: this is the guard that stops a cancel
# committed during the tool call from being overwritten by the outcome.
_EXECUTING_STATUSES = frozenset({"executing"})


def process_task(task_id: str, outbox_id: str, payload: dict[str, Any]) -> None:
    """RQ entrypoint: execute one approved task (synchronous worker context).

    An in-process executor binds this process's API loop, so the job runs there
    and shares the app's connection pool; a standalone ``rq worker`` has no
    bound loop and spawns one per job, as it owns its own process.
    """
    loop = get_runtime_loop()
    if loop is not None and loop.is_running():
        asyncio.run_coroutine_threadsafe(_process(task_id, outbox_id, payload), loop).result()
        return
    asyncio.run(_process(task_id, outbox_id, payload))


async def _process(
    task_id: str,
    outbox_id: str,
    payload: dict[str, Any],
    tool_proxy: ToolProxy | None = None,
    lease: ExecutionLease | None = None,
) -> None:
    """Take the dispatch lease, then load the task and dispatch to its handler.

    ``tool_proxy`` and ``lease`` are injectable for tests; production creates
    its own proxy and leases the dispatch record in the shared store.
    """
    lease = lease or ExecutionLease(outbox_id)
    if not await asyncio.to_thread(lease.acquire):
        # The holder renews its lease while the job runs, so a taken key means
        # a live worker owns this dispatch: this delivery is dropped, it does
        # not wait. RQ's requeue and the rescue scan are the recovery path, and
        # both only get the lease once the holder's lease expired.
        log.info("process_task_duplicate_skipped", task_id=task_id, outbox_id=outbox_id)
        return
    lease.start()
    owns_proxy = tool_proxy is None
    proxy = tool_proxy or ToolProxy()
    try:
        async with async_session_factory() as session:
            task = await session.get(Task, uuid.UUID(task_id))
            if task is None:
                log.warning("process_task_not_found", task_id=task_id)
                return
            if task.status not in _RUNNABLE_STATUSES:
                log.info("process_task_skipped", task_id=task_id, status=task.status)
                return
            if task.type != "tool_approval":
                await _fail_unsupported(session, task)
                return
            await _execute_tool(session, task, payload, proxy, lease)
    finally:
        # Off the event loop: a renewal stuck on an unreachable store must not
        # freeze the loop the in-process executor shares with the API.
        await asyncio.to_thread(lease.stop)
        if owns_proxy:
            await proxy.aclose()


async def _transition(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    to_status: str,
    from_statuses: frozenset[str],
    values: dict[str, Any] | None = None,
) -> bool:
    """Move a task only while it still holds one of ``from_statuses``.

    Same optimistic guard as ``TaskService.transition``: whoever loses the race
    sees rowcount 0 and must stop touching the row. Returns True when this
    caller moved it; the commit happens here so callers only emit afterwards.
    """
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(Task)
            .where(Task.id == task_id, Task.status.in_(from_statuses))
            .values(status=to_status, **(values or {}))
            .execution_options(synchronize_session=False)
        ),
    )
    await session.commit()
    return result.rowcount == 1


async def _emit_transition(task_id: uuid.UUID, from_status: str, to_status: str) -> None:
    """Announce a committed transition; the executor has no acting user."""
    if from_status == to_status:
        return
    await emit_task_status_changed_best_effort(
        TaskStatusChangedEvent(
            task_id=str(task_id), from_status=from_status, to_status=to_status, actor_id=None
        )
    )


async def _finish_failed(
    session: AsyncSession, task_id: uuid.UUID, from_status: str, message: str
) -> None:
    """Terminate a running task, without overwriting a cancel.

    Only ``executing`` may become ``failed`` (C-73), so this is the guard that
    keeps an approved task from skipping the middle state.
    """
    moved = await _transition(
        session,
        task_id,
        to_status="failed",
        from_statuses=_EXECUTING_STATUSES,
        values={"error_message": message, "resolved_at": datetime.now(UTC)},
    )
    if moved:
        await _emit_transition(task_id, from_status, "failed")
    else:
        log.info("process_task_terminal_skipped", task_id=str(task_id), attempted="failed")


async def _fail_before_execution(session: AsyncSession, task: Task, message: str) -> None:
    """Fail a task that cannot start, through the only legal route (C-73).

    ``已批准 → 失败`` is not a legal transition, so the row passes through
    ``executing`` first — the same path a run that dies at its first step takes.
    """
    if task.status == "approved":
        if not await _transition(
            session, task.id, to_status="executing", from_statuses=_RUNNABLE_STATUSES
        ):
            # A cancel landed first: the user's terminal decision stands.
            log.info("process_task_cancelled_before_start", task_id=str(task.id))
            return
        await _emit_transition(task.id, "approved", "executing")
    await _finish_failed(session, task.id, "executing", message)


async def _fail_unsupported(session: AsyncSession, task: Task) -> None:
    """Terminate a task whose type has no handler yet, instead of dropping it."""
    await _fail_before_execution(session, task, "不支持的任务类型")


class _LeaseLost(Exception):
    """The dispatch lease moved to another worker; this call must not record."""


# How often a running call checks that it still owns the dispatch. Loss is
# recorded by the heartbeat, so this bounds how long the old holder keeps a
# possibly duplicated call open.
_LEASE_POLL_SECONDS = 0.5


async def _call_tool(
    proxy: ToolProxy,
    tool: ToolRegistry,
    payload: dict[str, Any],
    lease: ExecutionLease,
) -> ExecutionResult:
    """Await the tool call, abandoning it the moment the lease is lost.

    Cancelling the request does not undo a side effect the upstream already
    performed — at-least-once permits the duplicate — but the old holder must
    not wait out a call whose outcome it may no longer write.
    """
    call = asyncio.create_task(proxy.execute(tool, payload.get("params", {})))
    try:
        while True:
            done, _ = await asyncio.wait({call}, timeout=_LEASE_POLL_SECONDS)
            if done:
                if lease.lost:
                    raise _LeaseLost
                return call.result()
            if lease.lost:
                raise _LeaseLost
    finally:
        if not call.done():
            call.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await call


async def _execute_tool(
    session: AsyncSession,
    task: Task,
    payload: dict[str, Any],
    proxy: ToolProxy,
    lease: ExecutionLease,
) -> None:
    """Run the approved tool and record the outcome on the task row."""
    task_id = task.id
    tool = await session.get(ToolRegistry, payload.get("tool_id"))
    if tool is None:
        await _fail_before_execution(session, task, f"Tool not found: {payload.get('tool_id')}")
        return

    # The status this delivery observed; also the event's from_status, since a
    # conditional move below only succeeds while the row still matches it.
    from_status = task.status
    moved = await _transition(
        session, task_id, to_status="executing", from_statuses=_RUNNABLE_STATUSES
    )
    if not moved:
        # A cancel landed between the load above and this write: the tool must
        # not run, or the user would see an external call after stopping it.
        log.info("process_task_cancelled_before_start", task_id=str(task_id))
        return
    await _emit_transition(task_id, from_status, "executing")

    if lease.lost:
        # Another delivery took the lease over and is responsible for the
        # outcome; this one must not add a second external call.
        log.warning("process_task_lease_lost_before_call", task_id=str(task_id))
        return

    values: dict[str, Any]
    try:
        # Approval is a decision about one request, not about the tool's current
        # mode: re-run the judgement so a tool disabled after approval is refused.
        ToolService.ensure_invocable(tool)
        result = await _call_tool(proxy, tool, payload, lease)
    except _LeaseLost:
        # Writing this outcome would race the new holder's row.
        log.warning("process_task_lease_lost_mid_call", task_id=str(task_id))
        return
    except Exception as exc:
        to_status = "failed"
        values = {"error_message": str(exc)}
    else:
        to_status = "completed"
        values = {
            "result": {
                "status_code": result.status_code,
                "body": result.body,
                "latency_ms": result.latency_ms,
            }
        }
    values["resolved_at"] = datetime.now(UTC)

    if await _transition(
        session, task_id, to_status=to_status, from_statuses=_EXECUTING_STATUSES, values=values
    ):
        await _emit_transition(task_id, "executing", to_status)
        return
    # Cancelled while the tool ran: cancelled is terminal, so the outcome is
    # dropped rather than written over the user's decision.
    current = await session.get(Task, task_id, populate_existing=True)
    log.warning(
        "process_task_outcome_discarded",
        task_id=str(task_id),
        attempted=to_status,
        status=current.status if current is not None else None,
    )
