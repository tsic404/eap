"""ConversationController: FastAPI router for the conversations resource (§32.4.3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DifyApiError
from app.db import get_session
from app.dependencies import get_current_user
from app.logging_conf import get_logger
from app.models.user import User
from app.schemas.conversation import (
    ConversationDto,
    ConversationPageDto,
    ConversationStatus,
    CreateConversationDto,
    ListConversationsDto,
    MessagePageDto,
    SendMessageDto,
)
from app.services.conversation import ConversationService, sse_frame

log = get_logger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

CurrentUser = Annotated[User, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


def get_conversation_service(request: Request) -> ConversationService:
    """Resolve the app-scoped conversation service (wired in ``create_app`` lifespan)."""
    return cast(ConversationService, request.app.state.conversation_service)


ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]


@router.get("", response_model=ConversationPageDto)
async def list_conversations(
    service: ConversationServiceDep,
    user: CurrentUser,
    session: Session,
    agent_id: Annotated[str | None, Query(alias="agentId")] = None,
    status: Annotated[ConversationStatus, Query()] = ConversationStatus.active,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> ConversationPageDto:
    query = ListConversationsDto(agentId=agent_id, status=status, limit=limit, cursor=cursor)
    return await service.list(session, user, query)


@router.post("", response_model=ConversationDto, status_code=201)
async def create_conversation(
    dto: CreateConversationDto,
    service: ConversationServiceDep,
    user: CurrentUser,
    session: Session,
) -> ConversationDto:
    return await service.create(session, user, dto)


@router.get("/{conversation_id}", response_model=ConversationDto)
async def get_conversation(
    conversation_id: str,
    service: ConversationServiceDep,
    user: CurrentUser,
    session: Session,
) -> ConversationDto:
    return await service.get(session, user, conversation_id)


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    service: ConversationServiceDep,
    user: CurrentUser,
    session: Session,
) -> None:
    await service.soft_delete(session, user, conversation_id)


@router.get("/{conversation_id}/messages", response_model=MessagePageDto)
async def list_messages(
    conversation_id: str,
    service: ConversationServiceDep,
    user: CurrentUser,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> MessagePageDto:
    return await service.list_messages(session, user, conversation_id, limit=limit, cursor=cursor)


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    dto: SendMessageDto,
    request: Request,
    service: ConversationServiceDep,
    user: CurrentUser,
    session: Session,
) -> StreamingResponse:
    conversation, agent = await service.prepare_stream(session, user, conversation_id, dto.agentId)

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for frame in service.stream(session, conversation, agent, user, dto):
                if await request.is_disconnected():
                    break
                yield frame
        except DifyApiError as exc:
            log.warning("sse_stream_error", conversation_id=conversation_id, error=str(exc))
            yield sse_frame("error", {"code": "STREAM_ERROR", "message": "Stream interrupted"})
        except Exception:
            log.exception("sse_stream_error", conversation_id=conversation_id)
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


@router.post("/{conversation_id}/tool-calls/{call_id}/confirm")
async def confirm_tool_call(
    conversation_id: str,
    call_id: str,
    service: ConversationServiceDep,
    user: CurrentUser,
    session: Session,
) -> dict[str, object]:
    return await service.confirm_tool_call(session, user, conversation_id, call_id)
