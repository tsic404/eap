"""Approval-expiry scan tests: which pending approvals the 15s scan retires (C-75)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models.task import Task
from app.models.tenant import Tenant
from app.models.user import User
from app.services.task_expiry import (
    TIMEOUT_RESULT_REASON,
    _cancel_expired,
    expire_overdue_approvals,
)


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


def _make_user(tenant: Tenant, role: str = "agent_admin") -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        name="User",
        role=role,
        status="active",
    )


async def _seed_tasks(session_factory, count: int, **overrides: object) -> list[uuid.UUID]:
    """Create tenant + creator and ``count`` identical tasks; return their ids."""
    tenant = _make_tenant()
    user = _make_user(tenant)
    async with session_factory() as session:
        session.add(tenant)
        session.add(user)
        await session.flush()
        fields: dict[str, object] = {
            "tenant_id": tenant.id,
            "creator_id": user.id,
            "type": "tool_approval",
            "title": "Approve tool",
            "priority": "high",
            "status": "pending",
            "payload": {"tool_id": "my-tool", "params": {}},
        }
        fields.update(overrides)
        tasks = [Task(**fields) for _ in range(count)]  # type: ignore[arg-type]
        session.add_all(tasks)
        await session.commit()
        return [task.id for task in tasks]


async def _read(session_factory, task_id: uuid.UUID) -> Task:
    async with session_factory() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        return task


@pytest.mark.asyncio
async def test_an_expired_pending_approval_is_cancelled_with_the_timeout_result(session_factory):
    (task_id,) = await _seed_tasks(
        session_factory, 1, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    await expire_overdue_approvals(session_factory)

    task = await _read(session_factory, task_id)
    assert task.status == "cancelled"
    assert task.result == {"reason": TIMEOUT_RESULT_REASON}


@pytest.mark.asyncio
async def test_a_pending_approval_inside_its_deadline_is_left_alone(session_factory):
    (task_id,) = await _seed_tasks(
        session_factory, 1, expires_at=datetime.now(UTC) + timedelta(hours=1)
    )

    await expire_overdue_approvals(session_factory)

    task = await _read(session_factory, task_id)
    assert task.status == "pending"
    assert task.result is None


@pytest.mark.asyncio
async def test_a_pending_task_without_a_deadline_is_left_alone(session_factory):
    (task_id,) = await _seed_tasks(session_factory, 1, type="knowledge_index")

    await expire_overdue_approvals(session_factory)

    assert (await _read(session_factory, task_id)).status == "pending"


@pytest.mark.asyncio
async def test_the_scan_ignores_a_task_that_is_not_pending(session_factory):
    (task_id,) = await _seed_tasks(
        session_factory,
        1,
        status="approved",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    await expire_overdue_approvals(session_factory)

    task = await _read(session_factory, task_id)
    assert task.status == "approved"
    assert task.result is None


@pytest.mark.asyncio
async def test_a_row_that_stopped_being_pending_is_not_overwritten(session_factory):
    # The race the conditional UPDATE exists for: by the time the scan writes the
    # row it read as pending, an approval has committed, so the write must match
    # nothing instead of pulling the approved task back into the terminal cancel.
    (task_id,) = await _seed_tasks(
        session_factory, 1, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    async with session_factory() as session:
        await session.execute(update(Task).where(Task.id == task_id).values(status="approved"))
        await session.commit()

    async with session_factory() as session:
        await _cancel_expired(session, task_id)

    task = await _read(session_factory, task_id)
    assert task.status == "approved"
    assert task.result is None


@pytest.mark.asyncio
async def test_a_scan_cancels_at_most_one_batch(session_factory):
    task_ids = await _seed_tasks(
        session_factory, 101, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    await expire_overdue_approvals(session_factory)

    async with session_factory() as session:
        result = await session.execute(
            select(Task.id).where(Task.id.in_(task_ids), Task.status == "cancelled")
        )
        assert len(list(result.scalars().all())) == 100

    # The leftover row is picked up by the next scan — no permanent loss.
    await expire_overdue_approvals(session_factory)

    async with session_factory() as session:
        result = await session.execute(
            select(Task.id).where(Task.id.in_(task_ids), Task.status == "cancelled")
        )
        assert len(list(result.scalars().all())) == 101
