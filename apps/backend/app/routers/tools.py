"""ToolController: tool CRUD + debug endpoints (architecture doc §32.5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import get_active_tenant, require_roles
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.tool import CreateToolDto, DebugToolDto, ToolRead, UpdateToolDto
from app.services.tool_service import ToolService

router = APIRouter(prefix="/api/tools", tags=["tools"])

_ADMIN_ROLES = ("platform_admin", "agent_admin")


def _service(request: Request, session: AsyncSession) -> ToolService:
    return ToolService(session, request.app.state.tool_proxy)


@router.get("", response_model=list[ToolRead])
async def list_tools(
    request: Request,
    tenant: Tenant = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_session),
) -> list[ToolRead]:
    service = _service(request, session)
    tools = await service.list(tenant)
    return [ToolRead.model_validate(tool) for tool in tools]


@router.get("/{tool_id}", response_model=ToolRead)
async def get_tool(
    tool_id: str,
    request: Request,
    tenant: Tenant = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_session),
) -> ToolRead:
    service = _service(request, session)
    tool = await service.get(tool_id, tenant)
    return ToolRead.model_validate(tool)


@router.post("", response_model=ToolRead, status_code=status.HTTP_201_CREATED)
async def create_tool(
    dto: CreateToolDto,
    request: Request,
    tenant: Tenant = Depends(get_active_tenant),
    user: User = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ToolRead:
    service = _service(request, session)
    tool = await service.create(dto, tenant, user)
    return ToolRead.model_validate(tool)


@router.patch("/{tool_id}", response_model=ToolRead)
async def update_tool(
    tool_id: str,
    dto: UpdateToolDto,
    request: Request,
    tenant: Tenant = Depends(get_active_tenant),
    user: User = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ToolRead:
    service = _service(request, session)
    tool = await service.update(tool_id, dto, tenant, user)
    return ToolRead.model_validate(tool)


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(
    tool_id: str,
    request: Request,
    tenant: Tenant = Depends(get_active_tenant),
    user: User = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> None:
    service = _service(request, session)
    await service.delete(tool_id, tenant, user)


@router.post("/{tool_id}/debug")
async def debug_tool(
    tool_id: str,
    dto: DebugToolDto,
    request: Request,
    tenant: Tenant = Depends(get_active_tenant),
    _: User = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    service = _service(request, session)
    tool = await service.get(tool_id, tenant)
    return await service.debug(tool, dto)
