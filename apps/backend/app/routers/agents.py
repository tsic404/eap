"""AgentController: FastAPI router for the agents resource (§32.2.3)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import get_active_tenant, get_current_user, require_roles
from app.errors import AppError
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.agent_repository import AgentRepository
from app.schemas.agent import (
    AgentDetailDto,
    AgentDto,
    CreateAgentDto,
    PublishAgentDto,
    UpdateAgentDto,
    agent_to_detail_dto,
    agent_to_dto,
)
from app.services.agent_service import AgentService

router = APIRouter(prefix="/api/agents", tags=["agents"])

_ADMIN_ROLES = ("agent_admin", "platform_admin")

CurrentUser = Annotated[User, Depends(get_current_user)]
ActiveTenant = Annotated[Tenant, Depends(get_active_tenant)]
AgentAdmin = Annotated[User, Depends(require_roles(*_ADMIN_ROLES))]


async def get_agent_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentService:
    return AgentService(session, request.app.state.dify_console)


AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]


@router.get("")
async def list_agents(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant: ActiveTenant,
    user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
    status: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    type_: Annotated[str | None, Query(alias="type")] = None,
    search: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    """List agents (paginated + filtered). Non-admins see only published ones."""
    repo = AgentRepository(session)
    effective_status = status if user.role in _ADMIN_ROLES else "published"
    agents, total = await repo.find_many(
        tenant.id,
        page=page,
        page_size=page_size,
        status=effective_status,
        category=category,
        type_=type_,
        search=search,
    )
    return {
        "items": [agent_to_dto(agent) for agent in agents],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


@router.get("/{agent_id}")
async def get_agent(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant: ActiveTenant,
    user: CurrentUser,
    agent_id: str,
) -> AgentDetailDto:
    """Return one agent with bound knowledge/tools and recent run logs."""
    repo = AgentRepository(session)
    agent = await repo.find_by_id(tenant.id, agent_id)
    if agent is None:
        raise AppError(404, "NOT_FOUND", "Agent not found")
    if user.role not in _ADMIN_ROLES and agent.status != "published":
        raise AppError(404, "NOT_FOUND", "Agent not found")

    recent_logs = await repo.find_recent_logs(tenant.id, agent_id)
    return agent_to_detail_dto(agent, recent_logs)


@router.post("", status_code=201)
async def create_agent(
    user: AgentAdmin,
    tenant: ActiveTenant,
    service: AgentServiceDep,
    body: CreateAgentDto,
) -> AgentDto:
    agent = await service.register(body, tenant_id=tenant.id, actor_id=user.id)
    return agent_to_dto(agent)


@router.patch("/{agent_id}")
async def update_agent(
    user: AgentAdmin,
    tenant: ActiveTenant,
    service: AgentServiceDep,
    agent_id: str,
    body: UpdateAgentDto,
) -> AgentDto:
    agent = await service.update(agent_id, body, tenant_id=tenant.id, actor_id=user.id)
    return agent_to_dto(agent)


@router.post("/{agent_id}/publish")
async def publish_agent(
    user: AgentAdmin,
    tenant: ActiveTenant,
    service: AgentServiceDep,
    agent_id: str,
    body: PublishAgentDto,
) -> AgentDto:
    agent = await service.publish(
        agent_id, body.version, tenant_id=tenant.id, actor_id=user.id
    )
    return agent_to_dto(agent)


@router.post("/{agent_id}/offline")
async def offline_agent(
    user: AgentAdmin,
    tenant: ActiveTenant,
    service: AgentServiceDep,
    agent_id: str,
) -> AgentDto:
    agent = await service.offline(agent_id, tenant_id=tenant.id, actor_id=user.id)
    return agent_to_dto(agent)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    user: AgentAdmin,
    tenant: ActiveTenant,
    service: AgentServiceDep,
    agent_id: str,
) -> None:
    await service.delete(agent_id, tenant_id=tenant.id, actor_id=user.id)
