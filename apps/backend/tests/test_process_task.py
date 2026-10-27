"""``process_task`` worker tests: execution, cancel guard, claim dedup, fallback."""

from __future__ import annotations

import asyncio
import importlib
import uuid

import httpx
import pytest
from sqlalchemy import update

from app.events.bus import bus
from app.events.task import TASK_STATUS_CHANGED_EVENT, TaskStatusChangedEvent
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.tool import ToolRegistry
from app.models.user import User
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


@pytest.mark.asyncio
async def test_process_task_executes_approved_tool(session_factory, monkeypatch):
    task_id = await _seed_task_and_tool(session_factory)
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)

    await _process(
        str(task_id),
        "outbox-1",
        {"tool_id": "my-tool", "params": {"a": 1}},
        tool_proxy=_proxy(),
        claim_execution=lambda _: True,
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
        claim_execution=lambda _: True,
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
            claim_execution=lambda _: True,
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
            claim_execution=lambda _: True,
        )
    finally:
        bus.unsubscribe(TASK_STATUS_CHANGED_EVENT, handler)

    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "failed"
        assert task.error_message == "Tool not found: deleted-tool"
    assert seen == [("approved", "executing"), ("executing", "failed")]


@pytest.mark.asyncio
async def test_process_task_duplicate_claim_skips_live_delivery(session_factory, monkeypatch):
    """A copy that loses the claim while another delivery is alive must not run."""
    task_id = await _seed_task_and_tool(session_factory)
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)

    await _process(
        str(task_id),
        "outbox-1",
        {"tool_id": "my-tool", "params": {"a": 1}},
        tool_proxy=_proxy(),
        claim_execution=lambda _: False,
        take_over_claim=lambda _: False,
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
        claim_execution=lambda _: True,
    )

    assert observed == ["executing"]


@pytest.mark.asyncio
async def test_process_task_takes_over_claim_left_by_crashed_delivery(session_factory, monkeypatch):
    """The claim a dead delivery never released must not swallow the requeued copy."""
    task_id = await _seed_task_and_tool(session_factory, status="executing")
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)
    taken_over: list[str] = []

    def _take_over(outbox_id: str) -> bool:
        taken_over.append(outbox_id)
        return True

    await _process(
        str(task_id),
        "outbox-1",
        {"tool_id": "my-tool", "params": {"a": 1}},
        tool_proxy=_proxy(),
        claim_execution=lambda _: False,
        take_over_claim=_take_over,
    )

    assert taken_over == ["outbox-1"]
    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "completed"
        assert task.result is not None


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
        await _execute_tool(session, task, {"tool_id": "my-tool", "params": {}}, _RecordingProxy())

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
        claim_execution=lambda _: True,
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
            claim_execution=lambda _: True,
        )
    finally:
        bus.unsubscribe(TASK_STATUS_CHANGED_EVENT, handler)

    assert seen == [("approved", "executing", None), ("executing", "completed", None)]
