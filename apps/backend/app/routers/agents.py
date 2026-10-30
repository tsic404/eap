"""AgentController: FastAPI router for the agents resource (§32.2.3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, cast

import structlog
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DifyApiError
from app.db import get_session
from app.dependencies import get_active_tenant, get_current_user, require_tenant_roles
from app.errors import AppError
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.agent_repository import AgentRepository
from app.schemas.agent import (
    AgentDetailDto,
    AgentDto,
    CreateAgentDto,
    DebugAgentMessageDto,
    PublishAgentDto,
    StartTestingDto,
    UpdateAgentDto,
    agent_to_detail_dto,
    agent_to_dto,
)
from app.services.agent_service import AgentService
from app.services.conversation import ConversationService, sse_frame

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

_ADMIN_ROLES = ("agent_admin", "platform_admin")

CurrentUser = Annotated[User, Depends(get_current_user)]
ActiveTenant = Annotated[Tenant, Depends(get_active_tenant)]
AgentAdmin = Annotated[User, Depends(require_tenant_roles(*_ADMIN_ROLES))]


async def get_agent_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentService:
    return AgentService(session, request.app.state.dify_console)


AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]


def get_conversation_service(request: Request) -> ConversationService:
    """Resolve the app-scoped stream service the debug turn reuses."""
    return cast(ConversationService, request.app.state.conversation_service)


ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]


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
    # The delete confirmation is admin-only; other callers never see tenant-wide
    # conversation volume (C-35 / C-53).
    conversation_count = (
        await repo.count_conversations(tenant.id, agent_id) if user.role in _ADMIN_ROLES else 0
    )
    return agent_to_detail_dto(agent, recent_logs, conversation_count)


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


@router.post("/{agent_id}/start-testing")
async def start_testing_agent(
    user: AgentAdmin,
    tenant: ActiveTenant,
    service: AgentServiceDep,
    agent_id: str,
    body: StartTestingDto,
) -> AgentDto:
    """Move a draft agent into testing (C-33's only entry path)."""
    agent = await service.start_testing(agent_id, body.version, tenant_id=tenant.id)
    return agent_to_dto(agent)


@router.post("/{agent_id}/publish")
async def publish_agent(
    user: AgentAdmin,
    tenant: ActiveTenant,
    service: AgentServiceDep,
    agent_id: str,
    body: PublishAgentDto,
) -> AgentDto:
    agent = await service.publish(agent_id, body.version, tenant_id=tenant.id, actor_id=user.id)
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


@router.post("/{agent_id}/debug")
async def debug_agent(
    agent_id: str,
    body: DebugAgentMessageDto,
    request: Request,
    user: AgentAdmin,
    tenant: ActiveTenant,
    service: ConversationServiceDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """Run one test message against the agent and stream the real model answer.

    The turn reuses the conversation stream pipeline — upstream normalisation,
    the platform event catalogue and the run-log write — without creating a
    conversation or message row: debugging is not a user conversation.
    """
    agent = await service.prepare_debug(session, tenant.id, agent_id)
    # The turn runs for as long as the model takes; release the request's
    # connection before it starts rather than holding it idle in a transaction.
    await session.close()

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for frame in service.stream_debug(agent, user, body.query):
                if await request.is_disconnected():
                    break
                yield frame
        except DifyApiError as exc:
            log.warning("debug_stream_error", agent_id=agent_id, error=str(exc))
            yield sse_frame("error", {"code": "STREAM_ERROR", "message": "Stream interrupted"})
        except Exception:
            log.exception("debug_stream_error", agent_id=agent_id)
            yield sse_frame("error", {"code": "STREAM_ERROR", "message": "Stream interrupted"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
