"""RQ worker job: execute an approved task (ApprovalConsumer).

Enqueued by ``app.services.task_service`` with a retry budget, so RQ puts a job
whose worker died back on the queue; the reconciler re-sends undelivered
dispatch records. This worker claims execution (Redis SETNX keyed on the
outbox id), takes a stale claim over when the task is still runnable, and
re-checks the status, so a cancelled task never runs. Every status write is a
conditional UPDATE, so a cancel that lands mid-flight is never overwritten.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from redis import Redis
from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import async_session_factory
from app.events.task import TaskStatusChangedEvent, emit_task_status_changed_best_effort
from app.models.task import Task
from app.models.tool import ToolRegistry
from app.services.tool_proxy import ToolProxy
from app.services.tool_service import ToolService
from app.workers.bridge import get_runtime_loop

log = structlog.get_logger(__name__)

# Statuses in which a task may still run. A cancelled/completed/failed/rejected
# task (status changed after enqueue) must be skipped, not executed.
_RUNNABLE_STATUSES = frozenset({"approved", "executing"})
# Only a running task may be finished: this is the guard that stops a cancel
# committed during the tool call from being overwritten by the outcome.
_EXECUTING_STATUSES = frozenset({"executing"})

# Execution-claim TTL: long enough to outlive any duplicate delivery (the
# reconciler's 15s window plus queue dwell), short enough to not leak keys.
_EXEC_CLAIM_TTL_SECONDS = 3600
_EXEC_CLAIM_KEY = "task:exec:{outbox_id}"

_redis_client: Redis | None = None


def _get_redis() -> Redis:
    """Return the process-wide Redis client, created lazily once."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(get_settings().redis_url)
    return _redis_client


def _claim_execution(outbox_id: str, client: Redis | None = None) -> bool:
    """Atomically claim execution for ``outbox_id`` (SETNX). True if first."""
    client = client or _get_redis()
    return bool(
        client.set(
            _EXEC_CLAIM_KEY.format(outbox_id=outbox_id),
            "1",
            nx=True,
            ex=_EXEC_CLAIM_TTL_SECONDS,
        )
    )


def _take_over_claim(outbox_id: str, client: Redis | None = None) -> bool:
    """Re-take the claim a delivery that died mid-job never released.

    The key absorbs duplicates while its holder is alive, but a crashed holder
    leaves it behind for the full idempotency window; refusing the requeued
    copy then loses the dispatch. The caller decides from the task row whether
    taking over is warranted, so this only swaps the key. Interleaving copies
    can both win and execute twice, which at-least-once already permits and
    the conditional status writes absorb.
    """
    client = client or _get_redis()
    key = _EXEC_CLAIM_KEY.format(outbox_id=outbox_id)
    client.delete(key)
    return bool(client.set(key, "1", nx=True, ex=_EXEC_CLAIM_TTL_SECONDS))


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
    claim_execution: Callable[[str], bool] | None = None,
    take_over_claim: Callable[[str], bool] | None = None,
) -> None:
    """Claim execution, then load the task and dispatch to its type handler.

    ``tool_proxy``, ``claim_execution`` and ``take_over_claim`` are injectable
    for tests; production creates its own proxy and uses the Redis claim.
    """
    claim = claim_execution or _claim_execution
    take_over = take_over_claim or _take_over_claim
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
            if not claim(outbox_id):
                # A stale claim looks exactly like a live duplicate: only the
                # task row tells them apart. A task that already reached a
                # terminal state is a duplicate of a finished delivery; one
                # still runnable has no live owner after the crash, so this is
                # the copy RQ requeued and it must run.
                if not take_over(outbox_id):
                    log.info("process_task_duplicate_skipped", task_id=task_id)
                    return
                log.warning("process_task_claim_taken_over", task_id=task_id, status=task.status)
            if task.type != "tool_approval":
                await _fail_unsupported(session, task)
                return
            await _execute_tool(session, task, payload, proxy)
    finally:
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


async def _execute_tool(
    session: AsyncSession,
    task: Task,
    payload: dict[str, Any],
    proxy: ToolProxy,
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

    values: dict[str, Any]
    try:
        # Approval is a decision about one request, not about the tool's current
        # mode: re-run the judgement so a tool disabled after approval is refused.
        ToolService.ensure_invocable(tool)
        result = await proxy.execute(tool, payload.get("params", {}))
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
