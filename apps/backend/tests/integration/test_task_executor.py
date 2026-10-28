"""In-process executor: the queue an approval fills is drained by the app itself.

``test_tool_approval_chain`` proves the outbox row and the enqueue; this one
proves the consumer the API process now owns picks the job up and drives the
task through ``executing`` to ``completed`` with the tool's response on the row.
Sessions here are real (no rollback wrapper) so the worker and the assertions
never share one connection.
"""

from __future__ import annotations

import asyncio
import importlib
import time
from typing import Any

import httpx
import pytest
from redis import Redis
from rq import Queue
from rq.executions import Execution
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.queue as queue_module
from app.config import Settings
from app.models.task import Task
from app.models.task_outbox import TaskOutboxEvent
from app.queue import _JOB_MAX_RETRIES
from app.services import task_service as task_service_module
from app.services.task_lease import LEASE_KEY
from app.services.task_service import TaskService
from app.services.tool_proxy import ToolProxy
from app.workers.executor import TaskExecutor
from tests.conftest import seed_task, seed_tenant_user, seed_tool

# ``app.workers.process_task`` resolves to the function re-exported by the
# package, so grab the module itself for attribute monkeypatching.
_PROCESS_TASK_MODULE = importlib.import_module("app.workers.process_task")


async def _wait_for_terminal(
    factory: async_sessionmaker[AsyncSession], task_id: Any, *, timeout: float = 15.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        async with factory() as session:
            task = await session.get(Task, task_id)
            assert task is not None
            snapshot = {
                "status": task.status,
                "result": task.result,
                "error_message": task.error_message,
                "resolved_at": task.resolved_at,
            }
        if snapshot["status"] in {"completed", "failed", "cancelled"}:
            return snapshot
        await asyncio.sleep(0.05)
    return snapshot


@pytest.mark.asyncio
async def test_executor_completes_approved_task(
    session_factory: async_sessionmaker[AsyncSession],
    redis_url: str,
    worker_index: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        tenant, admin = await seed_tenant_user(
            session, role="agent_admin", worker_index=worker_index
        )
        await seed_tool(session, tenant)
        pending = await seed_task(session, tenant, admin)
        task_id = pending.id

    redis_conn = Redis.from_url(redis_url)

    def _enqueue(
        task_id: str, outbox_id: str, queue_name: str, payload: dict[str, Any], queue: Any = None
    ) -> None:
        del queue  # route the dispatch at the executor's Redis instead of the default
        Queue(queue_name, connection=redis_conn).enqueue(
            "app.workers.process_task.process_task",
            task_id,
            outbox_id,
            payload,
            job_id=outbox_id,
        )

    # The job body has to write through a real session, the tool HTTP boundary
    # stays mocked, and the dispatch lease uses the executor's Redis.
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)
    monkeypatch.setattr(
        _PROCESS_TASK_MODULE,
        "ToolProxy",
        lambda: ToolProxy(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))
        ),
    )
    monkeypatch.setattr("app.services.task_lease._open_redis", lambda: redis_conn)

    async def _approve() -> None:
        async with session_factory() as session:
            await TaskService(session).transition(
                str(task_id), "approved", actor=admin, tenant_id=tenant.id
            )

    monkeypatch.setattr(task_service_module, "enqueue_process_task", _enqueue)

    executor = TaskExecutor(Settings(_env_file=None, redis_url=redis_url))
    executor.start()
    try:
        await _approve()
        snapshot = await _wait_for_terminal(session_factory, task_id)
        if snapshot["status"] != "completed":
            failures = [
                (job.get_status(), job.exc_info)
                for job in Queue("tool-approval", connection=redis_conn).get_jobs()
            ]
            pytest.fail(f"task stuck in {snapshot['status']!r}; jobs={failures}")
    finally:
        await executor.stop()
        redis_conn.close()

    assert snapshot["status"] == "completed"
    assert snapshot["error_message"] is None
    assert snapshot["result"] is not None
    assert snapshot["result"]["status_code"] == 200
    assert snapshot["result"]["body"] == {"ok": True}
    assert snapshot["resolved_at"] is not None


def test_queue_names_cover_task_dispatch() -> None:
    """The executor listens on every queue a task type maps to."""
    from app.queue import QUEUE_NAME_MAP
    from app.workers.executor import TASK_QUEUES

    dispatched = set(QUEUE_NAME_MAP.values()) - {"audit-log"}
    assert dispatched == set(TASK_QUEUES)


@pytest.mark.asyncio
async def test_executor_finishes_task_abandoned_by_a_dead_worker(
    session_factory: async_sessionmaker[AsyncSession],
    redis_url: str,
    worker_index: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RQ's requeue plus the lease expiry must still converge to 已完成.

    Reproduces the crash the review found: the worker leased the dispatch and
    moved the task to 执行中, then died, leaving the lease (until its TTL runs
    out), an expired started-registry entry and the requeued job behind. The
    requeued copy must run once the lease expired, not by taking over a lease
    its holder might still be renewing.
    """
    async with session_factory() as session:
        tenant, admin = await seed_tenant_user(
            session, role="agent_admin", worker_index=worker_index
        )
        await seed_tool(session, tenant)
        pending = await seed_task(session, tenant, admin)
        task_id = pending.id

    redis_conn = Redis.from_url(redis_url)
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)
    monkeypatch.setattr(
        _PROCESS_TASK_MODULE,
        "ToolProxy",
        lambda: ToolProxy(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))
        ),
    )
    monkeypatch.setattr("app.services.task_lease._open_redis", lambda: redis_conn)
    # The real dispatch path, so the enqueue carries its retry budget.
    monkeypatch.setattr(
        queue_module, "_task_queue", lambda name: Queue(name, connection=redis_conn)
    )

    async with session_factory() as session:
        await TaskService(session).transition(
            str(task_id), "approved", actor=admin, tenant_id=tenant.id
        )
        outbox_id = (
            await session.execute(
                select(TaskOutboxEvent.id).where(TaskOutboxEvent.task_id == str(task_id))
            )
        ).scalar_one()

    queue = Queue("tool-approval", connection=redis_conn)
    job = queue.fetch_job(outbox_id)
    assert job is not None
    assert job.retries_left == _JOB_MAX_RETRIES
    # The dead worker had already dequeued it; the job hash outlives the pop.
    redis_conn.lrem(queue.key, 0, job.id)

    # The dead worker's leftovers: the lease it stopped renewing (it survives
    # only until its TTL), the 执行中 write it committed, and a started-registry
    # entry RQ will retire as abandoned.
    lease_key = LEASE_KEY.format(outbox_id=outbox_id)
    redis_conn.set(lease_key, "dead-worker", ex=1)
    async with session_factory() as session:
        await session.execute(update(Task).where(Task.id == task_id).values(status="executing"))
        await session.commit()
    deadline = time.monotonic() + 3
    while redis_conn.exists(lease_key) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert redis_conn.exists(lease_key) == 0
    with redis_conn.pipeline() as pipeline:
        Execution.create(job, 0, pipeline=pipeline)
        pipeline.execute()

    queue.started_job_registry.cleanup()

    assert queue.count == 1
    job.refresh()
    assert job.retries_left == _JOB_MAX_RETRIES - 1

    executor = TaskExecutor(Settings(_env_file=None, redis_url=redis_url))
    executor.start()
    try:
        snapshot = await _wait_for_terminal(session_factory, task_id)
    finally:
        await executor.stop()
        redis_conn.close()

    assert snapshot["status"] == "completed"
    assert snapshot["error_message"] is None
    assert snapshot["result"] is not None
    assert snapshot["result"]["status_code"] == 200
