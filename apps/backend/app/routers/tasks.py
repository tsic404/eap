"""TaskController: task list + approve/reject/retry/cancel (architecture §32.7.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import get_active_tenant, get_current_user, require_roles
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.task import ApproveTaskDto, RejectTaskDto, TaskListRead, TaskRead
from app.services.task_service import TaskService

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_ADMIN_ROLES = ("platform_admin", "agent_admin")


@router.get("", response_model=TaskListRead)
async def list_tasks(
    status_filter: str | None = Query(default=None, alias="status"),
    type_filter: str | None = Query(default=None, alias="type"),
    priority: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    tenant: Tenant = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_session),
) -> TaskListRead:
    service = TaskService(session)
    items, next_cursor = await service.list(
        tenant.id,
        status_filter=status_filter,
        type_filter=type_filter,
        priority=priority,
        limit=limit,
        cursor=cursor,
    )
    return TaskListRead(
        items=[TaskRead.model_validate(task) for task in items],
        next_cursor=next_cursor,
    )


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: str,
    tenant: Tenant = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_session),
) -> TaskRead:
    service = TaskService(session)
    task = await service.get(task_id, tenant.id)
    return TaskRead.model_validate(task)


@router.post("/{task_id}/approve", response_model=TaskRead)
async def approve_task(
    task_id: str,
    dto: ApproveTaskDto,
    tenant: Tenant = Depends(get_active_tenant),
    user: User = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> TaskRead:
    service = TaskService(session)
    task = await service.transition(
        task_id,
        "approved",
        actor_id=str(user.id),
        tenant_id=tenant.id,
        metadata=dto.comment,
    )
    return TaskRead.model_validate(task)


@router.post("/{task_id}/reject", response_model=TaskRead)
async def reject_task(
    task_id: str,
    dto: RejectTaskDto,
    tenant: Tenant = Depends(get_active_tenant),
    user: User = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> TaskRead:
    service = TaskService(session)
    task = await service.transition(
        task_id,
        "rejected",
        actor_id=str(user.id),
        tenant_id=tenant.id,
        metadata=dto.reason,
    )
    return TaskRead.model_validate(task)


@router.post("/{task_id}/retry", response_model=TaskRead)
async def retry_task(
    task_id: str,
    tenant: Tenant = Depends(get_active_tenant),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TaskRead:
    service = TaskService(session)
    task = await service.retry(task_id, actor_id=str(user.id), tenant_id=tenant.id)
    return TaskRead.model_validate(task)


@router.post("/{task_id}/cancel", response_model=TaskRead)
async def cancel_task(
    task_id: str,
    tenant: Tenant = Depends(get_active_tenant),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TaskRead:
    service = TaskService(session)
    task = await service.transition(
        task_id,
        "cancelled",
        actor_id=str(user.id),
        tenant_id=tenant.id,
    )
    return TaskRead.model_validate(task)
