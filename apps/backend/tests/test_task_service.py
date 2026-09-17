"""TaskService tests: state machine, optimistic lock, outbox, and ownership."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.models.task import Task
from app.models.task_outbox import TaskOutboxEvent
from app.models.tenant import Tenant
from app.models.user import User
from app.services.task_service import TaskService, decode_cursor, encode_cursor


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


async def _seed_task(
    session_factory, **overrides: object
) -> tuple[uuid.UUID, uuid.UUID, User]:
    """Create tenant + creator user + task; return (task_id, tenant_id, creator)."""
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
            "payload": {"tool_id": "my-tool", "params": {"a": 1}},
        }
        fields.update(overrides)
        task = Task(**fields)  # type: ignore[arg-type]
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id, tenant.id, user


def _capture_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    captured: list[dict[str, object]] = []

    def fake_enqueue(*, task_id: str, outbox_id: str, queue_name: str, payload: dict) -> None:
        captured.append(
            {
                "task_id": task_id,
                "outbox_id": outbox_id,
                "queue_name": queue_name,
                "payload": payload,
            }
        )

    monkeypatch.setattr("app.services.task_service.enqueue_process_task", fake_enqueue)
    return captured


@pytest.mark.asyncio
async def test_approve_transitions_writes_outbox_and_enqueues(session_factory, monkeypatch):
    task_id, tenant_id, user = await _seed_task(session_factory)
    captured = _capture_enqueue(monkeypatch)

    async with session_factory() as session:
        service = TaskService(session)
        task = await service.transition(
            str(task_id), "approved", tenant_id=tenant_id, actor=user
        )

    assert task.status == "approved"

    # Outbox row written in the same transaction, then claimed (delivered) by
    # the atomic dispatch before the enqueue.
    async with session_factory() as session:
        outbox = await session.get(TaskOutboxEvent, captured[0]["outbox_id"])
        assert outbox is not None
        assert outbox.task_id == str(task_id)
        assert outbox.type == "tool-approval"
        assert outbox.status == "approved"
        assert outbox.delivered_at is not None

    assert captured[0]["queue_name"] == "tool-approval"
    assert captured[0]["payload"] == {"tool_id": "my-tool", "params": {"a": 1}}


@pytest.mark.asyncio
async def test_approve_enqueue_failure_leaves_outbox_undelivered(session_factory, monkeypatch):
    """Enqueue-first dispatch: an enqueue failure must NOT claim the outbox,
    so the reconciler can retry (no permanent loss)."""
    task_id, tenant_id, user = await _seed_task(session_factory)

    def failing_enqueue(*, task_id: str, outbox_id: str, queue_name: str, payload: dict) -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.services.task_service.enqueue_process_task", failing_enqueue)

    async with session_factory() as session:
        service = TaskService(session)
        task = await service.transition(str(task_id), "approved", tenant_id=tenant_id, actor=user)

    assert task.status == "approved"
    async with session_factory() as session:
        result = await session.execute(
            select(TaskOutboxEvent).where(TaskOutboxEvent.task_id == str(task_id))
        )
        outbox = result.scalar_one()
        assert outbox.delivered_at is None


@pytest.mark.asyncio
async def test_approve_invalid_transition_raises_422(session_factory, monkeypatch):
    task_id, tenant_id, user = await _seed_task(session_factory, status="completed")
    _capture_enqueue(monkeypatch)

    async with session_factory() as session:
        service = TaskService(session)
        with pytest.raises(AppError) as exc:
            await service.transition(str(task_id), "approved", tenant_id=tenant_id, actor=user)
    assert exc.value.status_code == 422
    assert exc.value.code == "INVALID_TRANSITION"


@pytest.mark.asyncio
async def test_approve_expired_raises_422(session_factory, monkeypatch):
    task_id, tenant_id, user = await _seed_task(
        session_factory, expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    _capture_enqueue(monkeypatch)

    async with session_factory() as session:
        service = TaskService(session)
        with pytest.raises(AppError) as exc:
            await service.transition(str(task_id), "approved", tenant_id=tenant_id, actor=user)
    assert exc.value.status_code == 422
    assert exc.value.code == "TASK_EXPIRED"


@pytest.mark.asyncio
async def test_transition_unknown_task_raises_404(session_factory, monkeypatch):
    _, tenant_id, user = await _seed_task(session_factory)
    _capture_enqueue(monkeypatch)

    async with session_factory() as session:
        service = TaskService(session)
        with pytest.raises(AppError) as exc:
            await service.transition(str(uuid.uuid4()), "approved", tenant_id=tenant_id, actor=user)
    assert exc.value.status_code == 404
    assert exc.value.code == "TASK_NOT_FOUND"


@pytest.mark.asyncio
async def test_reject_persists_reason(session_factory, monkeypatch):
    task_id, tenant_id, user = await _seed_task(session_factory)
    _capture_enqueue(monkeypatch)

    async with session_factory() as session:
        service = TaskService(session)
        task = await service.transition(
            str(task_id), "rejected", tenant_id=tenant_id, actor=user, metadata="not allowed"
        )

    assert task.status == "rejected"
    assert task.result == {"reason": "not allowed"}


@pytest.mark.asyncio
async def test_cancel_writes_no_outbox_and_no_enqueue(session_factory, monkeypatch):
    task_id, tenant_id, user = await _seed_task(session_factory)
    captured = _capture_enqueue(monkeypatch)

    async with session_factory() as session:
        service = TaskService(session)
        task = await service.transition(str(task_id), "cancelled", tenant_id=tenant_id, actor=user)

    assert task.status == "cancelled"
    assert captured == []
    async with session_factory() as session:
        result = await session.execute(
            select(TaskOutboxEvent).where(TaskOutboxEvent.task_id == str(task_id))
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_cancel_by_non_owner_raises_403(session_factory, monkeypatch):
    tenant = _make_tenant()
    owner = _make_user(tenant, role="employee")
    intruder = _make_user(tenant, role="employee")
    async with session_factory() as session:
        session.add(tenant)
        session.add(owner)
        session.add(intruder)
        await session.flush()
        task = Task(
            tenant_id=tenant.id,
            creator_id=owner.id,
            type="tool_approval",
            title="t",
            priority="high",
            status="pending",
            payload={"tool_id": "my-tool"},
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

    _capture_enqueue(monkeypatch)
    async with session_factory() as session:
        service = TaskService(session)
        with pytest.raises(AppError) as exc:
            await service.transition(str(task.id), "cancelled", tenant_id=tenant.id, actor=intruder)
    assert exc.value.status_code == 403
    assert exc.value.code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_retry_increments_and_reenqueues(session_factory, monkeypatch):
    task_id, tenant_id, user = await _seed_task(session_factory, status="failed", retry_count=1)
    captured = _capture_enqueue(monkeypatch)

    async with session_factory() as session:
        service = TaskService(session)
        task = await service.retry(str(task_id), tenant_id=tenant_id, actor=user)

    assert task.status == "executing"
    assert task.retry_count == 2
    assert captured[0]["queue_name"] == "tool-approval"


@pytest.mark.asyncio
async def test_retry_by_non_owner_raises_403(session_factory, monkeypatch):
    tenant = _make_tenant()
    owner = _make_user(tenant, role="employee")
    intruder = _make_user(tenant, role="employee")
    async with session_factory() as session:
        session.add(tenant)
        session.add(owner)
        session.add(intruder)
        await session.flush()
        task = Task(
            tenant_id=tenant.id,
            creator_id=owner.id,
            type="tool_approval",
            title="t",
            priority="high",
            status="failed",
            payload={"tool_id": "my-tool"},
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

    _capture_enqueue(monkeypatch)
    async with session_factory() as session:
        service = TaskService(session)
        with pytest.raises(AppError) as exc:
            await service.retry(str(task.id), tenant_id=tenant.id, actor=intruder)
    assert exc.value.status_code == 403
    assert exc.value.code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_retry_exceeding_max_raises_409(session_factory, monkeypatch):
    task_id, tenant_id, user = await _seed_task(
        session_factory, status="failed", retry_count=3, max_retries=3
    )
    _capture_enqueue(monkeypatch)

    async with session_factory() as session:
        service = TaskService(session)
        with pytest.raises(AppError) as exc:
            await service.retry(str(task_id), tenant_id=tenant_id, actor=user)
    assert exc.value.status_code == 409
    assert exc.value.code == "MAX_RETRIES_EXCEEDED"


@pytest.mark.asyncio
async def test_retry_wrong_status_raises_422(session_factory, monkeypatch):
    task_id, tenant_id, user = await _seed_task(session_factory, status="pending")
    _capture_enqueue(monkeypatch)

    async with session_factory() as session:
        service = TaskService(session)
        with pytest.raises(AppError) as exc:
            await service.retry(str(task_id), tenant_id=tenant_id, actor=user)
    assert exc.value.status_code == 422
    assert exc.value.code == "INVALID_TRANSITION"


@pytest.mark.asyncio
async def test_get_scopes_to_tenant(session_factory):
    task_id, tenant_id, _ = await _seed_task(session_factory)

    async with session_factory() as session:
        service = TaskService(session)
        task = await service.get(str(task_id), tenant_id)
        assert task.id == task_id

    async with session_factory() as session:
        service = TaskService(session)
        with pytest.raises(AppError) as exc:
            await service.get(str(task_id), uuid.uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_paginates_and_filters(session_factory):
    tenant = _make_tenant()
    user = _make_user(tenant)
    async with session_factory() as session:
        session.add(tenant)
        session.add(user)
        await session.flush()
        for i in range(3):
            session.add(
                Task(
                    tenant_id=tenant.id,
                    creator_id=user.id,
                    type="tool_approval",
                    title=f"task-{i}",
                    priority="high",
                    status="pending" if i < 2 else "approved",
                    payload={"tool_id": "my-tool"},
                )
            )
        await session.commit()

    async with session_factory() as session:
        service = TaskService(session)

        pending, _ = await service.list(tenant.id, status_filter="pending", limit=20)
        assert len(pending) == 2
        assert {t.status for t in pending} == {"pending"}

        page1, next_cursor = await service.list(tenant.id, limit=2)
        assert len(page1) == 2
        assert next_cursor is not None

        page2, next_cursor2 = await service.list(tenant.id, limit=2, cursor=next_cursor)
        assert len(page2) == 1
        assert next_cursor2 is None

        page1_ids = {t.id for t in page1}
        page2_ids = {t.id for t in page2}
        assert page1_ids.isdisjoint(page2_ids)
        assert len(page1_ids | page2_ids) == 3


@pytest.mark.asyncio
async def test_transition_conflict_raises_409_and_rolls_back(monkeypatch):
    """Optimistic lock: a conditional UPDATE matching 0 rows -> 409, no outbox."""
    tenant = _make_tenant()
    actor = _make_user(tenant)
    task = Task(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        creator_id=actor.id,
        type="tool_approval",
        title="t",
        priority="high",
        status="pending",
        payload={"tool_id": "my-tool"},
    )

    session = AsyncMock()
    session.add = Mock()
    session.begin = Mock(return_value=_FakeBegin(session))

    select_result = AsyncMock()
    select_result.scalar_one_or_none = Mock(return_value=task)
    update_result = Mock()
    update_result.rowcount = 0
    session.execute = AsyncMock(side_effect=[select_result, update_result])

    _capture_enqueue(monkeypatch)

    service = TaskService(session)
    with pytest.raises(AppError) as exc:
        await service.transition(str(task.id), "approved", tenant_id=tenant.id, actor=actor)
    assert exc.value.status_code == 409
    assert exc.value.code == "TASK_ALREADY_PROCESSED"
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


class _FakeBegin:
    """Async context-manager stand-in for ``AsyncSession.begin``."""

    def __init__(self, session: AsyncMock) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if exc_type is None:
            await self.session.commit()
        else:
            await self.session.rollback()
        return False


def test_cursor_roundtrip() -> None:
    now = datetime.now(UTC)
    task_id = uuid.uuid4()
    token = encode_cursor(now, task_id)
    assert decode_cursor(token) == (now, task_id)


def test_cursor_invalid_raises_400() -> None:
    with pytest.raises(AppError) as exc:
        decode_cursor("not-a-valid-cursor")
    assert exc.value.status_code == 400
    assert exc.value.code == "INVALID_CURSOR"
