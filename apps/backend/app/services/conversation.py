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
from app.core.exceptions import DifyApiError
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
    MessageAttachmentDto,
    MessageDto,
    MessagePageDto,
    SendMessageDto,
)
from app.services.dify_client import DifyClientService
from app.services.dify_conversation_adapter import DifyConversationAdapter
from app.services.memory import MemoryService

log = structlog.get_logger(__name__)

_ADMIN_ROLES = ("agent_admin", "platform_admin")

# Truncation-notice policy (§31.2.3): the backend drives the token-limit banner
# from the conversation's Dify message count instead of the frontend counting
# rendered bubbles. One Dify message is one query/answer turn, so the threshold
# is expressed in turns.
_TRUNCATION_NOTICE_MESSAGE_THRESHOLD = 30
_MESSAGES_PAGE_LIMIT = 100
# The pre-stream count must not stall the turn: a single attempt with a short
# timeout keeps the worst-case Dify failure path to ~5s instead of the default
# 3-retry × 30s backoff (~2min) the generic GET path would incur.
_MESSAGE_COUNT_TIMEOUT_SECONDS = 5.0


async def _conversation_is_truncated(
    client: DifyClientService, conversation_id: str, user: str
) -> bool:
    """Return True when a Dify conversation has reached the truncation-notice threshold.

    Counts the conversation's message turns via Dify's history endpoint (one
    request; ``has_more`` short-circuits past the threshold). Best-effort by
    design: any failure yields ``False`` so a counting error never mis-flags a
    healthy conversation or delays the stream.
    """
    if not conversation_id:
        return False
    try:
        page = await client.get(
            "/v1/messages",
            query={
                "conversation_id": conversation_id,
                "user": user,
                "limit": str(_MESSAGES_PAGE_LIMIT),
            },
            retries=0,
            timeout=_MESSAGE_COUNT_TIMEOUT_SECONDS,
        )
    except DifyApiError:
        return False
    messages = page.get("data")
    if not isinstance(messages, list):
        return False
    if page.get("has_more") is True:
        return True
    return len(messages) >= _TRUNCATION_NOTICE_MESSAGE_THRESHOLD


def sse_frame(event: str, data: dict[str, Any]) -> str:
    """Serialize one platform SSE frame (``event:`` + ``data:`` lines)."""
    return (
        f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


class _EventPublisher:
    """Adapt the EventBus ``emit(name, **payload)`` to the adapter's ``publish(name, payload)``.

    The adapter only knows the Dify-side ids and the message content; the
    run-log write (consumed from ``conversation.completed``) also needs the
    agent/user/model context held by this service, so it is merged in here.
    """

    def __init__(
        self,
        event_bus: EventBus,
        *,
        agent_id: str,
        agent_name: str | None,
        user_name: str,
        input_text: str,
        model_name: str | None,
    ) -> None:
        self._bus = event_bus
        self._agent_id = agent_id
        self._agent_name = agent_name
        self._user_name = user_name
        self._input = input_text
        self._model_name = model_name

    async def publish(self, event_name: str, payload: dict[str, Any]) -> None:
        payload.update(
            {
                "agentId": self._agent_id,
                "agentName": self._agent_name,
                "userName": self._user_name,
                "input": self._input,
                "modelName": self._model_name,
            }
        )
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

    # ── message history ──

    async def list_messages(
        self,
        session: AsyncSession,
        user: User,
        conversation_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> MessagePageDto:
        """Return a conversation's message history from Dify, newest first.

        Messages live in Dify (the platform stores only the conversation row),
        so this proxies Dify's ``GET /v1/messages``. A conversation with no
        ``dify_conversation_id`` has exchanged no turn yet, so it returns an
        empty page without an upstream call.
        """
        conversation = await self._require_conversation(session, user, conversation_id)
        if not conversation.dify_conversation_id:
            return MessagePageDto(items=[], nextCursor=None)
        agent = await self._require_agent(session, user.tenant_id, conversation.agent_id)
        if not agent.dify_api_key:
            raise AppError(409, "AGENT_API_KEY_MISSING", "Agent has no API key")

        client = DifyClientService(self._settings.dify_api_base_url, agent.dify_api_key)
        query: dict[str, str] = {
            "conversation_id": conversation.dify_conversation_id,
            # The Dify ``user`` identifier must match the one used to post
            # messages (``<tenant_id>:<user_id>``) or Dify returns no history.
            "user": f"{user.tenant_id}:{user.id}",
            "limit": str(limit),
        }
        if cursor:
            query["first_id"] = cursor
        try:
            payload = await client.get("/v1/messages", query)
        except DifyApiError as exc:
            log.warning("message_history_error", conversation_id=conversation_id, error=str(exc))
            raise AppError(502, "DIFY_ERROR", "Failed to load message history") from exc
        finally:
            await client.aclose()

        items = [_to_message_dto(record) for record in payload.get("data") or []]
        next_cursor = items[-1].id if (payload.get("has_more") and items) else None
        return MessagePageDto(items=items, nextCursor=next_cursor)

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
        truncation_notice = await _conversation_is_truncated(
            client, conversation.dify_conversation_id or "", str(user.id)
        )
        adapter = DifyConversationAdapter(
            client,
            MemoryService(session),
            _EventPublisher(
                self._bus,
                agent_id=agent.agent_id,
                agent_name=agent.name,
                user_name=user.name,
                input_text=dto.query,
                model_name=agent.model_name,
            ),
        )
        files = [f.model_dump() for f in dto.files] if dto.files is not None else None
        try:
            async for event in adapter.stream_messages(
                conversation.dify_conversation_id or "",
                dto.query,
                str(user.id),
                str(user.tenant_id),
                files,
                truncation_notice=truncation_notice,
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


def _to_message_dto(record: dict[str, Any]) -> MessageDto:
    """Map one Dify history message onto the platform ``MessageDto``."""
    rating = (record.get("feedback") or {}).get("rating")
    return MessageDto(
        id=str(record.get("id") or ""),
        query=str(record.get("query") or ""),
        answer=str(record.get("answer") or ""),
        status=str(record.get("status") or "normal"),
        feedback=rating if rating in ("like", "dislike") else None,
        files=[
            MessageAttachmentDto(
                id=str(f.get("id") or ""),
                type=str(f.get("type") or "document"),
                url=f.get("url"),
                belongsTo=f.get("belongs_to"),
            )
            for f in (record.get("message_files") or [])
        ],
        createdAt=_epoch_to_datetime(record.get("created_at")),
    )


def _epoch_to_datetime(value: Any) -> datetime | None:
    """Convert a Dify Unix-epoch-seconds timestamp to an aware ``datetime``.

    Returns ``None`` for a missing, malformed, or out-of-range timestamp rather
    than forging one: a corrupt record must never surface a fabricated time.
    """
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None
