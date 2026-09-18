"""``process_task`` worker tests: execution, cancel guard, claim dedup, fallback."""

from __future__ import annotations

import importlib
import uuid

import httpx
import pytest

from app.models.task import Task
from app.models.tenant import Tenant
from app.models.tool import ToolRegistry
from app.models.user import User
from app.services.tool_proxy import ToolProxy
from app.workers.process_task import _process

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
    task_id = await _seed_task_and_tool(session_factory, task_type="knowledge_index")
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)

    await _process(
        str(task_id),
        "outbox-1",
        {"tool_id": "my-tool"},
        tool_proxy=_proxy(),
        claim_execution=lambda _: True,
    )

    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "failed"
        assert task.error_message == "Unsupported task type: knowledge_index"


@pytest.mark.asyncio
async def test_process_task_duplicate_claim_skips_execution(session_factory, monkeypatch):
    """A second job for the same outbox id must not re-execute (SETNX lost)."""
    task_id = await _seed_task_and_tool(session_factory)
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", session_factory)

    await _process(
        str(task_id),
        "outbox-1",
        {"tool_id": "my-tool", "params": {"a": 1}},
        tool_proxy=_proxy(),
        claim_execution=lambda _: False,
    )

    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task.status == "approved"
        assert task.result is None
