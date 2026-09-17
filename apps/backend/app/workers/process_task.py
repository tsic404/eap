"""RQ worker job: execute an approved task (ApprovalConsumer).

Enqueued by ``app.services.task_service`` onto the queue named by the task type
(see ``app.queue.QUEUE_NAME_MAP``). The job payload is the task's ``payload``
plus ``task_id``/``outbox_id``. Dispatch is at-least-once (see the reconciler),
so this worker claims execution atomically via a Redis ``SETNX`` keyed on the
outbox id — a duplicate job for the same outbox row is a no-op — and then
re-checks the task status so a task cancelled after dispatch never runs.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import async_session_factory
from app.models.task import Task
from app.models.tool import ToolRegistry
from app.services.tool_proxy import ToolProxy

log = structlog.get_logger(__name__)

# Statuses in which a task may still run. A cancelled/completed/failed/rejected
# task (status changed after enqueue) must be skipped, not executed.
_RUNNABLE_STATUSES = frozenset({"approved", "executing"})

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


def process_task(task_id: str, outbox_id: str, payload: dict[str, Any]) -> None:
    """RQ entrypoint: execute one approved task (synchronous worker context)."""
    asyncio.run(_process(task_id, outbox_id, payload))


async def _process(
    task_id: str,
    outbox_id: str,
    payload: dict[str, Any],
    tool_proxy: ToolProxy | None = None,
    claim_execution: Callable[[str], bool] | None = None,
) -> None:
    """Claim execution, then load the task and dispatch to its type handler.

    ``tool_proxy`` and ``claim_execution`` are injectable for tests; production
    creates its own proxy and uses the Redis SETNX claim.
    """
    claim = claim_execution or _claim_execution
    if not claim(outbox_id):
        log.info("process_task_duplicate_skipped", task_id=task_id, outbox_id=outbox_id)
        return

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
            await _execute_tool(session, task, payload, proxy)
    finally:
        if owns_proxy:
            await proxy.aclose()


async def _fail_unsupported(session: AsyncSession, task: Task) -> None:
    """Terminate a task whose type has no handler yet, instead of dropping it."""
    task.status = "failed"
    task.error_message = f"Unsupported task type: {task.type}"
    task.resolved_at = datetime.now(UTC)
    await session.commit()


async def _execute_tool(
    session: AsyncSession,
    task: Task,
    payload: dict[str, Any],
    proxy: ToolProxy,
) -> None:
    """Run the approved tool and record the outcome on the task row."""
    tool = await session.get(ToolRegistry, payload.get("tool_id"))
    if tool is None:
        task.status = "failed"
        task.error_message = f"Tool not found: {payload.get('tool_id')}"
        task.resolved_at = datetime.now(UTC)
        await session.commit()
        return

    task.status = "executing"
    await session.commit()

    try:
        result = await proxy.execute(tool, payload.get("params", {}))
    except Exception as exc:
        task.status = "failed"
        task.error_message = str(exc)
    else:
        task.status = "completed"
        task.result = {
            "status_code": result.status_code,
            "body": result.body,
            "latency_ms": result.latency_ms,
        }
    task.resolved_at = datetime.now(UTC)
    await session.commit()
