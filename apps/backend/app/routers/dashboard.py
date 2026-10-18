"""DashboardController: admin + user dashboard reads (architecture §15.1.2).

Both endpoints are read-only and tenant-scoped: the tenant verdict (existence,
status, quota) is reached before the role verdict on the admin route, and every
route leaves the tenant gate in the graph. The service constrains every query to
``user.tenant_id`` (and the user's own id for the user view), so no cross-tenant
data can leak.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import get_active_tenant, get_current_user, require_tenant_roles
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.dashboard import AdminDashboardDto, UserHomeDto
from app.services.dashboard import DashboardService

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_ADMIN_ROLES = ("platform_admin", "agent_admin")


@router.get("/admin", response_model=AdminDashboardDto)
async def get_admin_dashboard(
    user: User = Depends(require_tenant_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> AdminDashboardDto:
    service = DashboardService(session)
    return await service.get_admin_dashboard(user.tenant_id)


@router.get("/user", response_model=UserHomeDto)
async def get_user_home(
    user: User = Depends(get_current_user),
    _tenant: Tenant = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_session),
) -> UserHomeDto:
    service = DashboardService(session)
    return await service.get_user_home(user.tenant_id, user.id)
