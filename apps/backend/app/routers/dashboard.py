"""DashboardController: admin + user dashboard reads (architecture §15.1.2).

Both endpoints are read-only and tenant-scoped: the service constrains every
query to ``user.tenant_id`` (and the user's own id for the user view), so no
cross-tenant data can leak. The admin route adds a role gate as the outer
defense-in-depth layer.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import get_current_user, require_roles
from app.models.user import User
from app.schemas.dashboard import AdminDashboardDto, UserHomeDto
from app.services.dashboard import DashboardService

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_ADMIN_ROLES = ("platform_admin", "agent_admin")


@router.get("/admin", response_model=AdminDashboardDto)
async def get_admin_dashboard(
    user: User = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> AdminDashboardDto:
    service = DashboardService(session)
    return await service.get_admin_dashboard(user.tenant_id)


@router.get("/user", response_model=UserHomeDto)
async def get_user_home(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserHomeDto:
    service = DashboardService(session)
    return await service.get_user_home(user.tenant_id, user.id)
