"""RunLogController: run-log list + detail (architecture §32.6.2).

Both endpoints are audit-only reads gated by ``require_roles`` on the four
non-employee admin/auditor roles; the route-level gate is the outer
defense-in-depth layer, while tenant scoping is enforced by passing
``user.tenant_id`` into the service (whose repository filters every query on
it).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import require_roles
from app.models.user import User
from app.schemas.run_log import RunLogDetailDto, RunLogPageDto
from app.services.run_log import RUN_LOG_READ_ROLES, RunLogService

router = APIRouter(prefix="/api/run-logs", tags=["run-logs"])


@router.get("", response_model=RunLogPageDto)
async def list_run_logs(
    agent_id: str | None = Query(default=None, alias="agentId"),
    conversation_id: str | None = Query(default=None, alias="conversationId"),
    status: str | None = Query(default=None),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: User = Depends(require_roles(*RUN_LOG_READ_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> RunLogPageDto:
    service = RunLogService(session)
    return await service.list(
        user.tenant_id,
        agent_id=agent_id,
        conv_id=conversation_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        cursor=cursor,
    )


@router.get("/{trace_id}", response_model=RunLogDetailDto)
async def get_run_log(
    trace_id: str,
    user: User = Depends(require_roles(*RUN_LOG_READ_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> RunLogDetailDto:
    service = RunLogService(session)
    return await service.get_by_trace_id(user.tenant_id, trace_id)
