"""Conversation application service: CRUD, SSE proxy, tool-call confirm (§32.4)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from sqlalchemy import func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import AppError
from app.events.bus import EventBus, bus
from app.models.agent import AgentRegistry
from app.models.conversation import Conversation
from app.models.task import Task
from app.models.user import User
from app.repositories.conversation import ConversationRepository
from app.schemas.conversation import (
    ConversationDto,
    ConversationPageDto,
    CreateConversationDto,
    ListConversationsDto,
    SendMessageDto,
)
from app.services.dify_client import DifyClientService
from app.services.dify_conversation_adapter import DifyConversationAdapter
from app.services.memory import MemoryService

log = structlog.get_logger(__name__)

_ADMIN_ROLES = ("agent_admin", "platform_admin")


def sse_frame(event: str, data: dict[str, Any]) -> str:
    """Serialize one platform SSE frame (``event:`` + ``data:`` lines)."""
    return (
        f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


class _EventPublisher:
    """Adapt the EventBus ``emit(name, **payload)`` to the adapter's ``publish(name, payload)``."""

    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus

    async def publish(self, event_name: str, payload: dict[str, Any]) -> None:
        await self._bus.emit(event_name, **payload)


class ConversationService:
    """Coordinates the conversation repository, Dify adapter, and memory recall."""

    def __init__(self, settings: Settings, event_bus: EventBus = bus) -> None:
        self._settings = settings
        self._bus = event_bus
        self._repo = ConversationRepository()

    # ── create / list / get / delete ──

    async def create(
        self, session: AsyncSession, user: User, dto: CreateConversationDto
    ) -> ConversationDto:
        agent = await self._require_agent(session, user.tenant_id, dto.agentId)
        if user.role not in _ADMIN_ROLES and agent.status != "published":
            # Mirror the agents list: unpublished agents are invisible to
            # non-admins, so they cannot start a conversation with one.
            raise AppError(404, "AGENT_NOT_FOUND", "Agent not found")
        conversation = await self._repo.create(
            session,
            tenant_id=user.tenant_id,
            user_id=user.id,
            agent_id=agent.agent_id,
            agent_name=agent.name,
            title=dto.title,
        )
        await session.commit()
        return _to_dto(conversation)

    async def list(
        self, session: AsyncSession, user: User, query: ListConversationsDto
    ) -> ConversationPageDto:
        rows, next_cursor = await self._repo.list_for_user(
            session,
            user_id=user.id,
            agent_id=query.agentId,
            status=query.status.value,
            limit=query.limit,
            cursor=query.cursor,
        )
        return ConversationPageDto(
            items=[_to_dto(row) for row in rows],
            nextCursor=next_cursor,
        )

    async def get(self, session: AsyncSession, user: User, conversation_id: str) -> ConversationDto:
        conversation = await self._require_conversation(session, user, conversation_id)
        return _to_dto(conversation)

    async def soft_delete(self, session: AsyncSession, user: User, conversation_id: str) -> None:
        conversation = await self._require_conversation(session, user, conversation_id)
        conversation.deleted_at = datetime.now(UTC)
        await session.commit()

    # ── streaming ──

    async def prepare_stream(
        self, session: AsyncSession, user: User, conversation_id: str, agent_id: str
    ) -> tuple[Conversation, AgentRegistry]:
        """Resolve and validate the stream's conversation + agent before SSE starts.

        Runs in the handler (not inside the generator) so ownership, agent
        mismatch, and missing-key failures surface as JSON errors rather than a
        half-open ``text/event-stream`` response.
        """
        conversation = await self._require_conversation(session, user, conversation_id)
        if agent_id != conversation.agent_id:
            raise AppError(422, "AGENT_MISMATCH", "agentId does not match the conversation")
        agent = await self._require_agent(session, user.tenant_id, conversation.agent_id)
        if not agent.dify_api_key:
            raise AppError(409, "AGENT_API_KEY_MISSING", "Agent has no API key")
        return conversation, agent

    async def stream(
        self,
        session: AsyncSession,
        conversation: Conversation,
        agent: AgentRegistry,
        user: User,
        dto: SendMessageDto,
    ) -> AsyncIterator[str]:
        """Proxy one turn through the Dify adapter, yielding SSE frames.

        The client is per-agent (each app has its own Service-API key) and is
        closed when the stream ends or is cancelled.
        """
        # ``prepare_stream`` guarantees the key is present; the cast preserves
        # that invariant for mypy without a duplicate runtime check.
        client = DifyClientService(self._settings.dify_api_base_url, cast(str, agent.dify_api_key))
        adapter = DifyConversationAdapter(
            client, MemoryService(session), _EventPublisher(self._bus)
        )
        files = [f.model_dump() for f in dto.files] if dto.files is not None else None
        try:
            async for event in adapter.stream_messages(
                conversation.dify_conversation_id or "",
                dto.query,
                str(user.id),
                str(user.tenant_id),
                files,
            ):
                data = event.get("data") or {}
                dify_conv_id = data.get("conversationId")
                if dify_conv_id and conversation.dify_conversation_id is None:
                    # Dify created the conversation lazily on this first turn;
                    # record its id so later turns continue it.
                    conversation.dify_conversation_id = dify_conv_id
                    await session.commit()
                yield sse_frame(event["event"], data)
        finally:
            await client.aclose()

    # ── tool-call confirm ──

    async def confirm_tool_call(
        self, session: AsyncSession, user: User, conversation_id: str, call_id: str
    ) -> dict[str, Any]:
        """Confirm a pending tool-approval task for this conversation.

        P1 mapping: a conversation's tool call awaiting approval is represented
        by a ``tool_approval`` task whose payload references this conversation;
        ``callId`` is that task's id. The conditional update fires only on a
        genuine ``pending → approved`` transition, so concurrent confirms cannot
        double-approve.
        """
        conversation = await self._require_conversation(session, user, conversation_id)
        try:
            task_uuid = uuid.UUID(call_id)
        except (ValueError, TypeError, AttributeError):
            raise AppError(404, "NOT_FOUND", "Tool call not found") from None

        stmt = (
            update(Task)
            .where(
                Task.id == task_uuid,
                Task.tenant_id == user.tenant_id,
                Task.type == "tool_approval",
                Task.status == "pending",
                # Mirror the task-module transition guard (§14.1): an expired
                # approval must never become ``approved``, otherwise the RQ
                # worker would execute the tool anyway.
                or_(Task.expires_at.is_(None), Task.expires_at > func.now()),
                Task.payload["conversation_id"].as_string() == str(conversation.id),
            )
            .values(status="approved", resolved_at=func.now())
            .returning(Task.id, Task.status)
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            raise AppError(404, "NOT_FOUND", "Tool call not found")
        await session.commit()
        return {"callId": str(row.id), "status": row.status}

    # ── helpers ──

    async def _require_conversation(
        self, session: AsyncSession, user: User, conversation_id: str
    ) -> Conversation:
        try:
            conv_uuid = uuid.UUID(conversation_id)
        except (ValueError, TypeError, AttributeError):
            raise AppError(404, "NOT_FOUND", "Conversation not found") from None
        conversation = await self._repo.get_for_user(session, conv_uuid, user.id)
        if conversation is None:
            raise AppError(404, "NOT_FOUND", "Conversation not found")
        return conversation

    async def _require_agent(
        self, session: AsyncSession, tenant_id: uuid.UUID, agent_id: str
    ) -> AgentRegistry:
        agent = await session.get(AgentRegistry, agent_id)
        if agent is None or agent.tenant_id != tenant_id:
            raise AppError(404, "AGENT_NOT_FOUND", "Agent not found")
        return agent


def _to_dto(conversation: Conversation) -> ConversationDto:
    return ConversationDto(
        id=str(conversation.id),
        title=conversation.title,
        agentId=conversation.agent_id,
        agentName=conversation.agent_name,
        status=conversation.status,
        createdAt=conversation.created_at,
        updatedAt=conversation.updated_at,
    )
