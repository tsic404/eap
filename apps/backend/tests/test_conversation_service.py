"""ConversationService tests: CRUD isolation, soft delete, stream, tool-call confirm."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.conversation as conversation_module
from app.config import Settings
from app.errors import AppError
from app.models.agent import AgentRegistry
from app.models.conversation import Conversation
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_memory import UserMemory
from app.schemas.conversation import (
    CreateConversationDto,
    ListConversationsDto,
    SendMessageDto,
)
from app.services.conversation import ConversationService
from app.services.memory import MemoryService


async def _seed_tenant_user(
    session: AsyncSession, *, role: str = "employee"
) -> tuple[Tenant, User]:
    tenant = Tenant(
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        sso_provider="local",
        status="active",
    )
    session.add(tenant)
    await session.flush()
    user = User(
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"user-{uuid.uuid4().hex[:8]}@acme.com",
        name="User",
        role=role,
        status="active",
    )
    session.add(user)
    await session.flush()
    return tenant, user


def _seed_agent(
    session: AsyncSession,
    tenant: Tenant,
    *,
    agent_id: str = "agent-1",
    status: str = "published",
    api_key: str | None = "app-secret",
) -> AgentRegistry:
    agent = AgentRegistry(
        agent_id=agent_id,
        tenant_id=tenant.id,
        dify_app_id=f"dify-{agent_id}",
        dify_api_key=api_key,
        name="Agent",
        type="chat",
        status=status,
        version=1,
    )
    session.add(agent)
    return agent


def _settings() -> Settings:
    return Settings(_env_file=None)


class _FakeClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def aclose(self) -> None:
        pass


class _FakeAdapter:
    """Records args and yields a fixed message → message_end sequence."""

    def __init__(self, client: Any, memory: Any, publisher: Any) -> None:
        self.client = client
        self.memory = memory
        self.publisher = publisher
        self.last: tuple[Any, ...] = ()

    async def stream_messages(
        self,
        conv_id: str,
        query: str,
        user_id: str,
        tenant_id: str,
        files: list[dict[str, Any]] | None = None,
    ):
        self.last = (conv_id, query, user_id, tenant_id, files)
        yield {"event": "message", "data": {"content": "hi", "conversationId": "dify-c1"}}
        yield {"event": "message_end", "data": {"traceId": "t1", "metadata": {}}}


@pytest.mark.asyncio
async def test_create_returns_dto(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        _seed_agent(session, tenant)
        await session.commit()

        dto = await ConversationService(_settings()).create(
            session, user, CreateConversationDto(agentId="agent-1", title="First")
        )

        assert dto.agentId == "agent-1"
        assert dto.agentName == "Agent"
        assert dto.title == "First"
        assert dto.status == "active"
        assert dto.createdAt is not None


@pytest.mark.asyncio
async def test_create_unpublished_agent_404_for_employee(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session, role="employee")
        _seed_agent(session, tenant, status="draft")
        await session.commit()

        with pytest.raises(AppError) as exc:
            await ConversationService(_settings()).create(
                session, user, CreateConversationDto(agentId="agent-1")
            )

        assert exc.value.status_code == 404
        assert exc.value.code == "AGENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_list_isolated_by_user(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant, alice = await _seed_tenant_user(session)
        bob = User(
            tenant_id=tenant.id,
            sso_sub="bob-sub",
            email="bob@acme.com",
            name="Bob",
            role="employee",
            status="active",
        )
        session.add(bob)
        _seed_agent(session, tenant)
        await session.flush()

        service = ConversationService(_settings())
        await service.create(session, alice, CreateConversationDto(agentId="agent-1"))
        await service.create(session, bob, CreateConversationDto(agentId="agent-1"))

        alice_page = await service.list(session, alice, ListConversationsDto())
        bob_page = await service.list(session, bob, ListConversationsDto())

        assert len(alice_page.items) == 1
        assert len(bob_page.items) == 1
        assert alice_page.items[0].id != bob_page.items[0].id


@pytest.mark.asyncio
async def test_soft_delete_hides_from_list(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        _seed_agent(session, tenant)
        await session.commit()
        service = ConversationService(_settings())
        created = await service.create(session, user, CreateConversationDto(agentId="agent-1"))

        await service.soft_delete(session, user, created.id)

        page = await service.list(session, user, ListConversationsDto())
        assert page.items == []


@pytest.mark.asyncio
async def test_get_other_users_conversation_404(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant, alice = await _seed_tenant_user(session)
        bob = User(
            tenant_id=tenant.id,
            sso_sub="bob-sub",
            email="bob@acme.com",
            name="Bob",
            role="employee",
            status="active",
        )
        session.add(bob)
        _seed_agent(session, tenant)
        await session.flush()
        service = ConversationService(_settings())
        created = await service.create(session, alice, CreateConversationDto(agentId="agent-1"))

        with pytest.raises(AppError) as exc:
            await service.get(session, bob, created.id)

        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_stream_serializes_and_persists_dify_id(
    session_factory,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    adapters: list[_FakeAdapter] = []

    def make_adapter(client: Any, memory: Any, publisher: Any) -> _FakeAdapter:
        adapter = _FakeAdapter(client, memory, publisher)
        adapters.append(adapter)
        return adapter

    monkeypatch.setattr(conversation_module, "DifyClientService", _FakeClient)
    monkeypatch.setattr(conversation_module, "DifyConversationAdapter", make_adapter)

    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        _seed_agent(session, tenant)
        await session.commit()
        service = ConversationService(_settings())
        created = await service.create(session, user, CreateConversationDto(agentId="agent-1"))

        conversation = await session.get(Conversation, uuid.UUID(created.id))
        agent = await session.get(AgentRegistry, "agent-1")
        frames = [
            frame
            async for frame in service.stream(
                session,
                conversation,
                agent,
                user,
                SendMessageDto(query="hello", agentId="agent-1"),
            )
        ]

        assert len(frames) == 2
        assert frames[0].startswith("event: message\n")
        assert '"conversationId":"dify-c1"' in frames[0]
        assert frames[1].startswith("event: message_end\n")
        assert conversation.dify_conversation_id == "dify-c1"

        # New conversation: the adapter is asked to auto-create (empty id) and
        # the tenant/user are passed as strings for the Dify ``user`` encoding.
        conv_id, query, user_id, tenant_id, files = adapters[0].last
        assert conv_id == ""
        assert query == "hello"
        assert user_id == str(user.id)
        assert tenant_id == str(user.tenant_id)
        assert files is None


@pytest.mark.asyncio
async def test_confirm_tool_call_approves_pending_task(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        _seed_agent(session, tenant)
        await session.commit()
        service = ConversationService(_settings())
        created = await service.create(session, user, CreateConversationDto(agentId="agent-1"))

        task = Task(
            tenant_id=tenant.id,
            creator_id=user.id,
            type="tool_approval",
            title="approve tool",
            priority="high",
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            payload={"tool_id": "tool-1", "conversation_id": created.id},
        )
        session.add(task)
        await session.commit()

        result = await service.confirm_tool_call(session, user, created.id, str(task.id))

        assert result == {"callId": str(task.id), "status": "approved"}
        await session.refresh(task)
        assert task.status == "approved"


@pytest.mark.asyncio
async def test_confirm_tool_call_rejects_expired_task(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        _seed_agent(session, tenant)
        await session.commit()
        service = ConversationService(_settings())
        created = await service.create(session, user, CreateConversationDto(agentId="agent-1"))

        task = Task(
            tenant_id=tenant.id,
            creator_id=user.id,
            type="tool_approval",
            title="approve tool",
            priority="high",
            status="pending",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
            payload={"tool_id": "tool-1", "conversation_id": created.id},
        )
        session.add(task)
        await session.commit()

        with pytest.raises(AppError) as exc:
            await service.confirm_tool_call(session, user, created.id, str(task.id))

        assert exc.value.status_code == 404
        await session.refresh(task)
        assert task.status == "pending"


@pytest.mark.asyncio
async def test_memory_recall_excludes_expired_memories(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        active = UserMemory(
            tenant_id=tenant.id,
            user_id=user.id,
            type="fact",
            content="prefers short answers",
            importance=1.0,
        )
        expired = UserMemory(
            tenant_id=tenant.id,
            user_id=user.id,
            type="preference",
            content="stale preference",
            importance=1.0,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        session.add_all([active, expired])
        await session.commit()

        memories = await MemoryService(session).recall(str(user.id), str(tenant.id), "hi")

        contents = [m["content"] for m in memories]
        assert "prefers short answers" in contents
        assert "stale preference" not in contents
