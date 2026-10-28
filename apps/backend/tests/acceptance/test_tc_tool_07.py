"""Acceptance case for the task state machine (design AC-36).

The criterion is negative: a task moves only along the legal edges, and a
rejected or cancelled task can never re-enter execution. Every
``(status, action)`` pair is exercised, so a single illegal edge that the
service lets through fails this module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.models.task import Task
from app.models.task_outbox import TaskOutboxEvent
from app.services.task_expiry import TIMEOUT_RESULT_REASON, expire_overdue_approvals

TC_ID = "TC-tool-07"

_STATUSES = ("pending", "approved", "rejected", "executing", "completed", "failed", "cancelled")
_ACTIONS = ("approve", "reject", "retry", "cancel")

# Contract C-73 transcribed: source status -> action -> target status. An
# action absent from a status's mapping is an illegal transition.
_LEGAL_TARGETS: dict[str, dict[str, str]] = {
    "pending": {"approve": "approved", "reject": "rejected", "cancel": "cancelled"},
    "approved": {"cancel": "cancelled"},
    "rejected": {},
    "executing": {"cancel": "cancelled"},
    "completed": {},
    "failed": {"retry": "executing", "cancel": "cancelled"},
    "cancelled": {},
}

# The status each action writes; retry is the only action whose message does
# not name its target (`Cannot retry a task in '<status>' state`).
_ACTION_TARGETS = {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}

_BODIES: dict[str, Any] = {
    "approve": {},
    "reject": {"reason": "风险不可接受"},
    "retry": None,
    "cancel": None,
}

_PAIRS = [(status, action) for status in _STATUSES for action in _ACTIONS]
_LEGAL_PAIRS = [(status, action) for status, action in _PAIRS if action in _LEGAL_TARGETS[status]]
_ILLEGAL_PAIRS = [
    (status, action) for status, action in _PAIRS if action not in _LEGAL_TARGETS[status]
]


async def _seed_task(
    api: Any,
    tenant: Any,
    creator: Any,
    *,
    status: str,
    expires_at: datetime | None = None,
) -> Task:
    task = Task(
        tenant_id=tenant.id,
        creator_id=creator.id,
        type="tool_approval",
        title="Approve tool",
        priority="high",
        status=status,
        payload={"tool_id": "erp", "params": {}},
        expires_at=expires_at,
    )
    async with api.db.session() as session:
        session.add(task)
        await session.commit()
    return task


async def _act(api: Any, headers: dict[str, str], task_id: str, action: str) -> httpx.Response:
    return await api.client.post(
        f"/api/tasks/{task_id}/{action}", headers=headers, json=_BODIES[action]
    )


async def _detail(api: Any, headers: dict[str, str], task_id: str) -> dict[str, Any]:
    resp = await api.client.get(f"/api/tasks/{task_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _outbox_rows(api: Any, task_id: str) -> list[TaskOutboxEvent]:
    async with api.db.session() as session:
        result = await session.execute(
            select(TaskOutboxEvent).where(TaskOutboxEvent.task_id == task_id)
        )
        return list(result.scalars().all())


async def _seed_admin_task(
    api: Any, status: str, *, expires_at: datetime | None = None
) -> tuple[dict[str, str], Task]:
    tenant, admin = await api.seed(role="agent_admin")
    task = await _seed_task(api, tenant, admin, status=status, expires_at=expires_at)
    return api.auth(admin, tenant), task


@pytest.mark.parametrize(("from_status", "action"), _LEGAL_PAIRS)
@pytest.mark.asyncio
async def test_allowed_transition_succeeds(api, from_status: str, action: str) -> None:
    headers, task = await _seed_admin_task(api, from_status)

    resp = await _act(api, headers, str(task.id), action)

    assert resp.status_code == 200, resp.text
    target = _LEGAL_TARGETS[from_status][action]
    assert resp.json()["data"]["status"] == target
    assert (await _detail(api, headers, str(task.id)))["status"] == target
    if action == "retry":
        assert resp.json()["data"]["retry_count"] == 1


@pytest.mark.parametrize(("from_status", "action"), _ILLEGAL_PAIRS)
@pytest.mark.asyncio
async def test_illegal_transition_is_refused(api, from_status: str, action: str) -> None:
    headers, task = await _seed_admin_task(api, from_status)

    resp = await _act(api, headers, str(task.id), action)

    assert resp.status_code == 422, resp.text
    error = resp.json()["error"]
    assert error["code"] == "INVALID_TRANSITION"
    # C-73: the message names both endpoints for anything but retry.
    assert from_status in error["message"]
    if action in _ACTION_TARGETS:
        assert _ACTION_TARGETS[action] in error["message"]

    # The refusal leaves the task untouched and dispatches nothing.
    assert (await _detail(api, headers, str(task.id)))["status"] == from_status
    assert api.enqueued == []
    assert await _outbox_rows(api, str(task.id)) == []


@pytest.mark.asyncio
async def test_a_rejected_task_can_never_reach_executing(api) -> None:
    headers, task = await _seed_admin_task(api, "pending")

    rejected = await _act(api, headers, str(task.id), "reject")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["status"] == "rejected"
    assert rejected.json()["data"]["result"] == {"reason": "风险不可接受"}
    assert api.enqueued == []

    for action in ("approve", "retry"):
        resp = await _act(api, headers, str(task.id), action)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "INVALID_TRANSITION"
        assert "rejected" in resp.json()["error"]["message"]

    assert (await _detail(api, headers, str(task.id)))["status"] == "rejected"
    assert api.enqueued == []


@pytest.mark.asyncio
async def test_a_timed_out_approval_can_never_reach_executing(api) -> None:
    headers, task = await _seed_admin_task(
        api, "pending", expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    # C-75: the 15s scanner retires the overdue approval into the cancelled
    # terminal state; the ASGI transport does not run the scheduler, so drive
    # the same scan the scheduled job runs.
    await expire_overdue_approvals(api.db.session)

    detail = await _detail(api, headers, str(task.id))
    assert detail["status"] == "cancelled"
    assert detail["result"] == {"reason": TIMEOUT_RESULT_REASON}

    for action in ("approve", "retry", "cancel"):
        resp = await _act(api, headers, str(task.id), action)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "INVALID_TRANSITION"
        assert "cancelled" in resp.json()["error"]["message"]

    assert (await _detail(api, headers, str(task.id)))["status"] == "cancelled"
    assert api.enqueued == []
