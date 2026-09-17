"""Agent module tests: Saga registration, optimistic lock, events, tenant isolation."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.db import get_session
from app.dify_console import DifyConsoleClient
from app.errors import AppError
from app.events.agent import AGENT_DELETED, AGENT_OFFLINE, AGENT_PUBLISHED
from app.events.bus import EventBus
from app.main import create_app
from app.models.agent import AgentRegistry
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.agent_repository import AgentRepository
from app.schemas.agent import CreateAgentDto, UpdateAgentDto
from app.services.agent_service import AgentService


def _console_mock(*, app_id: str = "dify-app-1", api_token: str = "app-secret-1") -> AsyncMock:
    console = AsyncMock(spec=DifyConsoleClient)
    console.create_app.return_value = {"id": app_id, "name": "app"}
    console.configure_model.return_value = {"result": "success"}
    console.create_api_key.return_value = {"id": "key-1", "token": api_token}
    console.delete_app.return_value = None
    return console


async def _seed_tenant_user(
    session, *, role: str = "agent_admin"  # type: ignore[no-untyped-def]
) -> tuple[Tenant, User]:
    slug = f"acme-{uuid.uuid4().hex[:8]}"
    tenant = Tenant(name="Acme", slug=slug, sso_provider="local", status="active")
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


def _make_keypair() -> tuple[rsa.RSAPrivateKey, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    return private_key, public_pem


async def _route_client(session_factory, *, role: str = "agent_admin") -> tuple[AsyncClient, str]:  # type: ignore[no-untyped-def]
    private_key, public_pem = _make_keypair()
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session, role=role)
        await session.commit()
        tenant_id = tenant.id
        user_id = user.id
        user_role = user.role

    app = create_app(
        Settings(_env_file=None, jwt_public_key=public_pem, rate_limit_enabled=False)
    )
    app.state.dify_console = _console_mock()

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    token = pyjwt.encode(
        {"sub": str(user_id), "tenantId": str(tenant_id), "role": user_role},
        private_key,
        algorithm="RS256",
    )
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test"), token


@pytest.mark.asyncio
async def test_register_creates_draft_agent_with_dify_app(session_factory) -> None:  # type: ignore[no-untyped-def]
    console = _console_mock()
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        service = AgentService(session, console)

        agent = await service.register(
            CreateAgentDto(
                agentId="sales-bot",
                name="Sales Bot",
                modelId="model-1",
                modelName="GPT-4o",
                modelProvider="openai",
            ),
            tenant_id=tenant.id,
            actor_id=user.id,
        )

        assert agent.agent_id == "sales-bot"
        assert agent.status == "draft"
        assert agent.dify_app_id == "dify-app-1"
        assert agent.dify_api_key == "app-secret-1"
        console.create_app.assert_awaited_once()
        console.configure_model.assert_awaited_once()
        console.create_api_key.assert_awaited_once()
        console.delete_app.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_compensates_dify_app_on_db_failure(session_factory) -> None:  # type: ignore[no-untyped-def]
    console = _console_mock()
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        service = AgentService(session, console)

        # The binding FK (kb_id -> knowledge_base_registry) rejects the insert,
        # which exercises the Saga compensation path (delete the Dify app) and
        # surfaces a client-facing 400 instead of a raw IntegrityError.
        with pytest.raises(AppError) as exc:
            await service.register(
                CreateAgentDto(
                    agentId="broken-bot", name="Broken", knowledgeBaseIds=["missing-kb"]
                ),
                tenant_id=tenant.id,
                actor_id=user.id,
            )

        assert exc.value.status_code == 400
        assert exc.value.code == "INVALID_BINDING"
        console.delete_app.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_duplicate_agent_id_raises_409(session_factory) -> None:  # type: ignore[no-untyped-def]
    console = _console_mock()
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        service = AgentService(session, console)
        dto = CreateAgentDto(agentId="dup-bot", name="Bot")
        await service.register(dto, tenant_id=tenant.id, actor_id=user.id)

        with pytest.raises(AppError) as exc:
            await service.register(dto, tenant_id=tenant.id, actor_id=user.id)

        assert exc.value.status_code == 409
        assert exc.value.code == "DUPLICATE_AGENT_ID"
        console.delete_app.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_version_conflict_raises_409(session_factory) -> None:  # type: ignore[no-untyped-def]
    console = _console_mock()
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        service = AgentService(session, console)
        agent = await service.register(
            CreateAgentDto(agentId="bot-1", name="Bot"),
            tenant_id=tenant.id,
            actor_id=user.id,
        )

        with pytest.raises(AppError) as exc:
            await service.publish(
                agent.agent_id,
                version=agent.version + 5,
                tenant_id=tenant.id,
                actor_id=user.id,
            )

        assert exc.value.status_code == 409
        assert exc.value.code == "CONFLICT"


@pytest.mark.asyncio
async def test_publish_sets_published_and_emits_event(session_factory) -> None:  # type: ignore[no-untyped-def]
    console = _console_mock()
    event_bus = EventBus()
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        service = AgentService(session, console, event_bus=event_bus)
        agent = await service.register(
            CreateAgentDto(agentId="bot-2", name="Bot"),
            tenant_id=tenant.id,
            actor_id=user.id,
        )

        published_events: list[dict[str, object]] = []

        async def on_published(sender: str, **payload: object) -> None:
            published_events.append(payload)

        event_bus.subscribe(AGENT_PUBLISHED, on_published)

        published = await service.publish(
            agent.agent_id,
            version=agent.version,
            tenant_id=tenant.id,
            actor_id=user.id,
        )

        assert published.status == "published"
        assert published.published_at is not None
        assert len(published_events) == 1
        assert published_events[0]["agent_id"] == "bot-2"


@pytest.mark.asyncio
async def test_find_many_is_tenant_scoped(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant_a, _ = await _seed_tenant_user(session, role="agent_admin")
        tenant_b, _ = await _seed_tenant_user(session, role="agent_admin")
        repo = AgentRepository(session)
        await repo.create(
            AgentRegistry(
                agent_id="a-bot", tenant_id=tenant_a.id, dify_app_id="d1", name="A Bot"
            )
        )
        await repo.create(
            AgentRegistry(
                agent_id="b-bot", tenant_id=tenant_b.id, dify_app_id="d2", name="B Bot"
            )
        )
        await session.commit()

        agents_a, total_a = await repo.find_many(
            tenant_a.id,
            page=1,
            page_size=20,
            status=None,
            category=None,
            type_=None,
            search=None,
        )

        assert [agent.agent_id for agent in agents_a] == ["a-bot"]
        assert total_a == 1


@pytest.mark.asyncio
async def test_update_applies_fields_with_optimistic_lock(session_factory) -> None:  # type: ignore[no-untyped-def]
    console = _console_mock()
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        service = AgentService(session, console)
        agent = await service.register(
            CreateAgentDto(agentId="bot-3", name="Old"),
            tenant_id=tenant.id,
            actor_id=user.id,
        )

        initial_version = agent.version
        updated = await service.update(
            agent.agent_id,
            UpdateAgentDto(version=initial_version, name="New", description="desc"),
            tenant_id=tenant.id,
            actor_id=user.id,
        )
        assert updated.name == "New"
        assert updated.description == "desc"
        assert updated.version == initial_version + 1

        with pytest.raises(AppError) as exc:
            await service.update(
                agent.agent_id,
                UpdateAgentDto(version=1, name="Stale"),
                tenant_id=tenant.id,
                actor_id=user.id,
            )
        assert exc.value.code == "CONFLICT"


@pytest.mark.asyncio
async def test_update_model_fields_maps_camelcase_to_columns(session_factory) -> None:  # type: ignore[no-untyped-def]
    console = _console_mock()
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        service = AgentService(session, console)
        agent = await service.register(
            CreateAgentDto(agentId="bot-model", name="Bot"),
            tenant_id=tenant.id,
            actor_id=user.id,
        )

        updated = await service.update(
            agent.agent_id,
            UpdateAgentDto(
                version=agent.version,
                modelId="model-1",
                modelName="GPT-4o",
                modelProvider="openai",
            ),
            tenant_id=tenant.id,
            actor_id=user.id,
        )

        # Regression: camelCase DTO keys must map to the ORM columns, otherwise
        # SQLAlchemy raises an unmapped-attribute error and PATCH 500s.
        assert updated.model_id == "model-1"
        assert updated.model_name == "GPT-4o"
        assert updated.model_provider == "openai"


@pytest.mark.asyncio
async def test_update_rejects_unknown_knowledge_binding(session_factory) -> None:  # type: ignore[no-untyped-def]
    console = _console_mock()
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        service = AgentService(session, console)
        agent = await service.register(
            CreateAgentDto(agentId="bot-bind", name="Bot"),
            tenant_id=tenant.id,
            actor_id=user.id,
        )

        # PATCH with a knowledgeBaseIds entry that does not exist violates the
        # binding FK; it must surface as 400 INVALID_BINDING, not a raw 500.
        with pytest.raises(AppError) as exc:
            await service.update(
                agent.agent_id,
                UpdateAgentDto(version=agent.version, knowledgeBaseIds=["missing-kb"]),
                tenant_id=tenant.id,
                actor_id=user.id,
            )

        assert exc.value.status_code == 400
        assert exc.value.code == "INVALID_BINDING"


def test_update_rejects_explicit_null_for_not_null_fields() -> None:
    with pytest.raises(ValueError):
        UpdateAgentDto(version=1, name=None)

    with pytest.raises(ValueError):
        UpdateAgentDto(version=1, tags=None)


@pytest.mark.asyncio
async def test_offline_and_delete_emit_events(session_factory) -> None:  # type: ignore[no-untyped-def]
    console = _console_mock()
    event_bus = EventBus()
    async with session_factory() as session:
        tenant, user = await _seed_tenant_user(session)
        service = AgentService(session, console, event_bus=event_bus)
        agent = await service.register(
            CreateAgentDto(agentId="bot-4", name="Bot"),
            tenant_id=tenant.id,
            actor_id=user.id,
        )

        offline_events: list[dict[str, object]] = []
        deleted_events: list[dict[str, object]] = []

        async def on_offline(sender: str, **payload: object) -> None:
            offline_events.append(payload)

        async def on_deleted(sender: str, **payload: object) -> None:
            deleted_events.append(payload)

        event_bus.subscribe(AGENT_OFFLINE, on_offline)
        event_bus.subscribe(AGENT_DELETED, on_deleted)

        offlined = await service.offline(
            agent.agent_id, tenant_id=tenant.id, actor_id=user.id
        )
        assert offlined.status == "offline"
        assert len(offline_events) == 1

        await service.delete(agent.agent_id, tenant_id=tenant.id, actor_id=user.id)
        assert len(deleted_events) == 1
        assert await AgentRepository(session).find_by_id(tenant.id, agent.agent_id) is None
        # Soft delete also removes the Dify app so it is no longer callable.
        console.delete_app.assert_awaited_once_with(agent.dify_app_id)


@pytest.mark.asyncio
async def test_create_agent_returns_201(session_factory) -> None:  # type: ignore[no-untyped-def]
    http, token = await _route_client(session_factory)
    async with http:
        resp = await http.post(
            "/api/agents",
            json={"agentId": "sales-bot", "name": "Sales Bot"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["data"]["status"] == "draft"
    assert body["data"]["difyAppId"] == "dify-app-1"


@pytest.mark.asyncio
async def test_create_agent_forbidden_for_employee(session_factory) -> None:  # type: ignore[no-untyped-def]
    http, token = await _route_client(session_factory, role="employee")
    async with http:
        resp = await http.post(
            "/api/agents",
            json={"agentId": "x-bot", "name": "X"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"
