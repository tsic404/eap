"""RQ worker job: execute an approved task (ApprovalConsumer).

Enqueued by ``app.services.task_service`` onto the queue named by the task type
(see ``app.queue.QUEUE_NAME_MAP``). The job payload is the task's ``payload``
plus ``task_id``/``outbox_id``; the outbox id is also the RQ ``job_id`` so the
reconciler and the main path never double-execute a task.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_factory
from app.models.task import Task
from app.models.tool import ToolRegistry
from app.services.tool_proxy import ToolProxy

log = structlog.get_logger(__name__)


def process_task(task_id: str, outbox_id: str, **payload: Any) -> None:
    """RQ entrypoint: execute one approved task (synchronous worker context)."""
    asyncio.run(_process(task_id, payload))


async def _process(
    task_id: str,
    payload: dict[str, Any],
    tool_proxy: ToolProxy | None = None,
) -> None:
    """Load the task and dispatch to the handler for its type.

    Only ``tool_approval`` has a producer in this codebase; the queue routing
    for other task types exists so their modules can attach handlers.
    ``tool_proxy`` is injectable for tests; production creates (and closes) its
    own.
    """
    owns_proxy = tool_proxy is None
    proxy = tool_proxy or ToolProxy()
    try:
        async with async_session_factory() as session:
            task = await session.get(Task, uuid.UUID(task_id))
            if task is None:
                log.warning("process_task_not_found", task_id=task_id)
                return
            if task.type != "tool_approval":
                log.warning("process_task_unsupported_type", task_id=task_id, type=task.type)
                return
            await _execute_tool(session, task, payload, proxy)
    finally:
        if owns_proxy:
            await proxy.aclose()


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
