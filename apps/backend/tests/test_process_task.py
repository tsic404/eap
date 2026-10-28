"""``process_task`` worker tests: execution, cancel guard, lease dedup, fallback."""

from __future__ import annotations

import asyncio
import importlib
import time
import uuid

import httpx
import pytest
from redis import Redis
from sqlalchemy import update

from app.events.bus import bus
from app.events.task import TASK_STATUS_CHANGED_EVENT, TaskStatusChangedEvent
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.tool import ToolRegistry
from app.models.user import User
from app.services.task_lease import LEASE_KEY, ExecutionLease
from app.services.tool_proxy import ExecutionResult, ToolProxy
from app.workers.bridge import bind_runtime_loop
from app.workers.process_task import _execute_tool, _process

# ``app.workers.process_task`` resolves to the *function* (re-exported by the
# package __init__), so grab the module itself for attribute monkeypatching.
_PROCESS_TASK_MODULE = importlib.import_module("app.workers.process_task")


def _make_tenant() -> Tenant:
    return Tenant(
        id=uuid.uuid4(),
        name="Tenant",
        slug=f"tenant-{uuid.uuid4().hex[:8]}",
        sso_provider="local",
        status="active",
        quota_limit=10000,
        quota_used=0,
    )


def _make_user(tenant: Tenant) -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        name="User",
        role="agent_admin",
        status="active",
    )


async def _seed_task_and_tool(
    session_factory, *, status: str = "approved", task_type: str = "tool_approval"
) -> uuid.UUID:
    tenant = _make_tenant()
    user = _make_user(tenant)
    async with session_factory() as session:
        session.add(tenant)
        session.add(user)
        await session.flush()
        session.add(
            ToolRegistry(
                tool_id="my-tool",
                tenant_id=None,
                name="My Tool",
                type="http",
                endpoint="http://tool.example/api",
                method="POST",
                risk_level="medium",
                permission_mode="auto",
                auth_type="none",
                timeout_ms=10000,
                retry_policy={"max_retries": 1, "base_delay_ms": 10},
                status="active",
            )
        )
        task = Task(
            tenant_id=tenant.id,
            creator_id=user.id,
            type=task_type,
            title="Approve tool",
            priority="high",
            status=status,
            payload={"tool_id": "my-tool", "params": {"a": 1}},
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


def _proxy() -> ToolProxy:
    return ToolProxy(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
    )


class _HeldLease:
    """Non-Redis stand-in for the execution lease.

    ``acquired`` decides the claim outcome; the unit cases below are about what
    the worker does with a won or lost lease, not about the store itself.
    """

    def __init__(self, *, acquired: bool = True) -> None:
        self.acquired = acquired
        self.started = False
        self.stopped = False
        self.lost = False

    def acquire(self) -> bool:
        return self.acquired

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_process_task_executes_approved_tool(session_factory, monkeypatch):
    task_id = await _seed_task_and_tool(session_factory)
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)

    await _process(
        str(task_id),
        "outbox-1",
        {"tool_id": "my-tool", "params": {"a": 1}},
        tool_proxy=_proxy(),
        lease=_HeldLease(),
    )

    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "completed"
        assert task.result is not None
        assert task.result["status_code"] == 200


@pytest.mark.asyncio
async def test_process_task_skips_cancelled_task(session_factory, monkeypatch):
    task_id = await _seed_task_and_tool(session_factory, status="cancelled")
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)

    await _process(
        str(task_id),
        "outbox-1",
        {"tool_id": "my-tool", "params": {"a": 1}},
        tool_proxy=_proxy(),
        lease=_HeldLease(),
    )

    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "cancelled"
        assert task.result is None


@pytest.mark.asyncio
async def test_process_task_marks_unsupported_type_failed(session_factory, monkeypatch):
    """C-73/C-77: an unknown type fails through 执行中, never straight from 已批准."""
    task_id = await _seed_task_and_tool(session_factory, task_type="knowledge_index")
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)
    seen: list[tuple[str, str]] = []

    async def handler(sender: str, **payload: object) -> None:
        event = payload["event"]
        assert isinstance(event, TaskStatusChangedEvent)
        seen.append((event.from_status, event.to_status))

    bus.subscribe(TASK_STATUS_CHANGED_EVENT, handler)
    try:
        await _process(
            str(task_id),
            "outbox-1",
            {"tool_id": "my-tool"},
            tool_proxy=_proxy(),
            lease=_HeldLease(),
        )
    finally:
        bus.unsubscribe(TASK_STATUS_CHANGED_EVENT, handler)

    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "failed"
        assert task.error_message == "不支持的任务类型"
    assert seen == [("approved", "executing"), ("executing", "failed")]


@pytest.mark.asyncio
async def test_process_task_fails_a_tool_deleted_before_execution(session_factory, monkeypatch):
    """A hard-deleted tool fails the task through 执行中, not from 已批准 (C-73)."""
    task_id = await _seed_task_and_tool(session_factory)
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)
    seen: list[tuple[str, str]] = []

    async def handler(sender: str, **payload: object) -> None:
        event = payload["event"]
        assert isinstance(event, TaskStatusChangedEvent)
        seen.append((event.from_status, event.to_status))

    bus.subscribe(TASK_STATUS_CHANGED_EVENT, handler)
    try:
        await _process(
            str(task_id),
            "outbox-1",
            {"tool_id": "deleted-tool", "params": {}},
            tool_proxy=_proxy(),
            lease=_HeldLease(),
        )
    finally:
        bus.unsubscribe(TASK_STATUS_CHANGED_EVENT, handler)

    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "failed"
        assert task.error_message == "Tool not found: deleted-tool"
    assert seen == [("approved", "executing"), ("executing", "failed")]


@pytest.mark.asyncio
async def test_process_task_duplicate_delivery_skips_a_live_lease(session_factory, monkeypatch):
    """A copy that cannot take the lease while its holder is alive must not run."""
    task_id = await _seed_task_and_tool(session_factory)
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)

    await _process(
        str(task_id),
        "outbox-1",
        {"tool_id": "my-tool", "params": {"a": 1}},
        tool_proxy=_proxy(),
        lease=_HeldLease(acquired=False),
    )

    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "approved"
        assert task.result is None


@pytest.mark.asyncio
async def test_process_task_runs_on_bound_loop(monkeypatch):
    """The in-process executor's bound loop runs the job, not a fresh one."""
    seen: list[tuple[str, str, dict]] = []

    async def record(task_id: str, outbox_id: str, payload: dict) -> None:
        seen.append((task_id, outbox_id, payload))

    monkeypatch.setattr(_PROCESS_TASK_MODULE, "_process", record)
    bind_runtime_loop(asyncio.get_running_loop())
    try:
        await asyncio.to_thread(
            _PROCESS_TASK_MODULE.process_task, "task-1", "outbox-1", {"tool_id": "t"}
        )
    finally:
        bind_runtime_loop(None)

    assert seen == [("task-1", "outbox-1", {"tool_id": "t"})]


@pytest.mark.asyncio
async def test_process_task_marks_executing_before_calling_tool(session_factory, monkeypatch):
    """AC-34's middle state is committed before the upstream call."""
    task_id = await _seed_task_and_tool(session_factory)
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)
    observed: list[str] = []

    class _ObservingProxy(ToolProxy):
        async def execute(self, tool, params):  # type: ignore[no-untyped-def]
            async with session_factory() as probe:
                task = await probe.get(Task, task_id)
                observed.append(task.status)
            return ExecutionResult(status_code=200, body={"ok": True}, latency_ms=1, attempts=1)

    await _process(
        str(task_id),
        "outbox-1",
        {"tool_id": "my-tool", "params": {"a": 1}},
        tool_proxy=_ObservingProxy(),
        lease=_HeldLease(),
    )

    assert observed == ["executing"]


@pytest.mark.asyncio
async def test_replayed_dispatch_waits_for_the_dead_holders_lease_to_expire(
    session_factory, monkeypatch, redis_url
):
    """A crash leaves the lease behind; only its expiry lets the replay run."""
    task_id = await _seed_task_and_tool(session_factory, status="executing")
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)
    client = Redis.from_url(redis_url)
    outbox_id = f"outbox-{uuid.uuid4().hex}"
    dead_worker = ExecutionLease(outbox_id, client=client, ttl_seconds=1)
    assert dead_worker.acquire()
    calls: list[str] = []

    class _RecordingProxy(ToolProxy):
        async def execute(self, tool, params):  # type: ignore[no-untyped-def]
            calls.append("executed")
            return ExecutionResult(status_code=200, body={"ok": True}, latency_ms=1, attempts=1)

    try:
        # The dead holder's key is still alive: a replay must not run the tool.
        await _process(
            str(task_id),
            outbox_id,
            {"tool_id": "my-tool", "params": {}},
            tool_proxy=_RecordingProxy(),
            lease=ExecutionLease(outbox_id, client=client, ttl_seconds=1),
        )
        assert calls == []

        deadline = time.monotonic() + 3
        while client.exists(LEASE_KEY.format(outbox_id=outbox_id)) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert client.exists(LEASE_KEY.format(outbox_id=outbox_id)) == 0

        await _process(
            str(task_id),
            outbox_id,
            {"tool_id": "my-tool", "params": {}},
            tool_proxy=_RecordingProxy(),
            lease=ExecutionLease(outbox_id, client=client),
        )
    finally:
        client.delete(LEASE_KEY.format(outbox_id=outbox_id))
        client.close()

    assert calls == ["executed"]
    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "completed"
        assert task.result is not None


@pytest.mark.asyncio
async def test_concurrent_deliveries_execute_the_tool_once(session_factory, monkeypatch, redis_url):
    """Two workers handed the same dispatch must not both call the tool."""
    task_id = await _seed_task_and_tool(session_factory)
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)
    client = Redis.from_url(redis_url)
    outbox_id = f"outbox-{uuid.uuid4().hex}"
    calls: list[str] = []
    running = asyncio.Event()
    release = asyncio.Event()

    async def _deliver() -> None:
        class _SlowProxy(ToolProxy):
            async def execute(self, tool, params):  # type: ignore[no-untyped-def]
                calls.append("executed")
                running.set()
                await release.wait()
                return ExecutionResult(status_code=200, body={"ok": True}, latency_ms=1, attempts=1)

        await _process(
            str(task_id),
            outbox_id,
            {"tool_id": "my-tool", "params": {}},
            tool_proxy=_SlowProxy(),
            lease=ExecutionLease(outbox_id, client=client),
        )

    first = asyncio.create_task(_deliver())
    try:
        await asyncio.wait_for(running.wait(), timeout=3)
        await _deliver()
        assert calls == ["executed"]
    finally:
        release.set()
        await first
        client.delete(LEASE_KEY.format(outbox_id=outbox_id))
        client.close()

    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "completed"
        assert task.result is not None


@pytest.mark.asyncio
async def test_lost_lease_abandons_the_running_call_without_writing(
    session_factory, monkeypatch, redis_url
):
    """A takeover must stop the old holder, not just leave a warning."""
    task_id = await _seed_task_and_tool(session_factory)
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)
    client = Redis.from_url(redis_url)
    outbox_id = f"outbox-{uuid.uuid4().hex}"
    started = asyncio.Event()
    cancelled: list[str] = []

    class _BlockingProxy(ToolProxy):
        async def execute(self, tool, params):  # type: ignore[no-untyped-def]
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append("cancelled")
                raise
            return ExecutionResult(status_code=200, body={"ok": True}, latency_ms=1, attempts=1)

    delivery = asyncio.create_task(
        _process(
            str(task_id),
            outbox_id,
            {"tool_id": "my-tool", "params": {}},
            tool_proxy=_BlockingProxy(),
            lease=ExecutionLease(outbox_id, client=client, ttl_seconds=1, renew_seconds=0.1),
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        # What a takeover after expiry leaves behind: the key no longer exists.
        client.delete(LEASE_KEY.format(outbox_id=outbox_id))
        await asyncio.wait_for(delivery, timeout=5)
    finally:
        if not delivery.done():
            delivery.cancel()
        client.delete(LEASE_KEY.format(outbox_id=outbox_id))
        client.close()

    assert cancelled == ["cancelled"]
    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "executing"
        assert task.result is None
        assert task.error_message is None


@pytest.mark.asyncio
async def test_execute_tool_stops_before_upstream_after_cancel(session_factory):
    """A cancel committed after the load wins: no external call, no overwrite."""
    task_id = await _seed_task_and_tool(session_factory)
    calls: list[str] = []

    class _RecordingProxy(ToolProxy):
        async def execute(self, tool, params):  # type: ignore[no-untyped-def]
            calls.append("executed")
            return ExecutionResult(status_code=200, body={"ok": True}, latency_ms=1, attempts=1)

    async with session_factory() as session:
        task = await session.get(Task, task_id)  # still approved in this session
        async with session_factory() as canceller:
            await canceller.execute(
                update(Task).where(Task.id == task_id).values(status="cancelled")
            )
            await canceller.commit()
        await _execute_tool(
            session, task, {"tool_id": "my-tool", "params": {}}, _RecordingProxy(), _HeldLease()
        )

    assert calls == []
    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "cancelled"
        assert task.result is None
        assert task.error_message is None


@pytest.mark.asyncio
async def test_process_task_discards_outcome_when_cancelled_mid_call(session_factory, monkeypatch):
    """A cancel landing while the tool runs is terminal; the result is dropped."""
    task_id = await _seed_task_and_tool(session_factory)
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)

    class _CancellingProxy(ToolProxy):
        async def execute(self, tool, params):  # type: ignore[no-untyped-def]
            async with session_factory() as canceller:
                await canceller.execute(
                    update(Task).where(Task.id == task_id).values(status="cancelled")
                )
                await canceller.commit()
            return ExecutionResult(status_code=200, body={"ok": True}, latency_ms=1, attempts=1)

    await _process(
        str(task_id),
        "outbox-1",
        {"tool_id": "my-tool", "params": {"a": 1}},
        tool_proxy=_CancellingProxy(),
        lease=_HeldLease(),
    )

    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "cancelled"
        assert task.result is None
        assert task.error_message is None


@pytest.mark.asyncio
async def test_process_task_emits_each_status_transition(session_factory, monkeypatch):
    """C-74: every transition the executor commits is announced, as the system."""
    task_id = await _seed_task_and_tool(session_factory)
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)
    seen: list[tuple[str, str, str | None]] = []

    async def handler(sender: str, **payload: object) -> None:
        event = payload["event"]
        assert isinstance(event, TaskStatusChangedEvent)
        seen.append((event.from_status, event.to_status, event.actor_id))

    bus.subscribe(TASK_STATUS_CHANGED_EVENT, handler)
    try:
        await _process(
            str(task_id),
            "outbox-1",
            {"tool_id": "my-tool", "params": {"a": 1}},
            tool_proxy=_proxy(),
            lease=_HeldLease(),
        )
    finally:
        bus.unsubscribe(TASK_STATUS_CHANGED_EVENT, handler)

    assert seen == [("approved", "executing", None), ("executing", "completed", None)]
