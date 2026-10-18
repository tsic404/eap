"""Member management endpoints: the platform-administrator roster (C-28).

Only a platform administrator reaches any of these paths; every one of them
works inside the caller's tenant context, so a member identifier from another
tenant is simply not found (C-29). The local-account paths exist only while the
deployment enables local login (C-110).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_body import json_object
from app.db import get_session
from app.dependencies import get_active_tenant, require_roles
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.member import MemberDto, MemberPageDto, MemberStatusUpdate, member_to_dto
from app.services.member_service import MemberService

router = APIRouter(prefix="/api/users", tags=["members"])

_PLATFORM_ADMIN_ROLE = "platform_admin"

PlatformAdmin = Annotated[User, Depends(require_roles(_PLATFORM_ADMIN_ROLE))]
ActiveTenant = Annotated[Tenant, Depends(get_active_tenant)]


def get_member_service(request: Request) -> MemberService:
    """Build the member service from the app's runtime wiring."""
    return MemberService(
        request.app.state.settings, refresh_service=request.app.state.refresh_service
    )


@router.get("", response_model=MemberPageDto)
async def list_members(
    service: Annotated[MemberService, Depends(get_member_service)],
    tenant: ActiveTenant,
    _: PlatformAdmin,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    search: str | None = None,
    role: str | None = None,
    status: str | None = None,
) -> MemberPageDto:
    """List the tenant's members, filtered by name/email, role and status."""
    members, total = await service.list_members(
        session,
        tenant,
        page=page,
        page_size=page_size,
        search=search,
        role=role,
        status=status,
    )
    return MemberPageDto(items=[member_to_dto(member) for member in members], total=total)


@router.get("/{member_id}", response_model=MemberDto)
async def get_member(
    member_id: str,
    service: Annotated[MemberService, Depends(get_member_service)],
    tenant: ActiveTenant,
    _: PlatformAdmin,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberDto:
    """Read one member of the tenant; another tenant's identifier is not found."""
    member = await service.get_member(session, tenant, member_id)
    return member_to_dto(member)


@router.post("", response_model=MemberDto, status_code=201)
async def create_member(
    request: Request,
    service: Annotated[MemberService, Depends(get_member_service)],
    tenant: ActiveTenant,
    _: PlatformAdmin,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberDto:
    """Create a local account for the tenant; it starts out needing a change."""
    body = await json_object(request)
    member = await service.create_local_account(session, tenant, body=body)
    return member_to_dto(member)


@router.patch("/{member_id}", response_model=MemberDto)
async def update_member(
    member_id: str,
    payload: MemberStatusUpdate,
    service: Annotated[MemberService, Depends(get_member_service)],
    tenant: ActiveTenant,
    actor: PlatformAdmin,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberDto:
    """Switch a member's status, enforcing the C-29 invariants on disable."""
    member = await service.set_status(session, tenant, actor, member_id, status=payload.status)
    return member_to_dto(member)


@router.post("/{member_id}/password", response_model=MemberDto)
async def reset_member_password(
    member_id: str,
    request: Request,
    service: Annotated[MemberService, Depends(get_member_service)],
    tenant: ActiveTenant,
    _: PlatformAdmin,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberDto:
    """Set a member's password to the one the administrator provides (C-115)."""
    body = await json_object(request)
    member = await service.reset_password(session, tenant, member_id, body=body)
    return member_to_dto(member)
