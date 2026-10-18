"""TC-tech-agent-01 — bindings are narrowed to the agent's tenant.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-agent-01 → AC-T03) over
``docs/02-contracts.md`` C-102 / C-14 / C-98, with scope item F1. A binding may
only reference an asset of the agent's own tenant, or a global tool that carries
no tenant at all (C-65); a cross-tenant id is answered exactly like a missing one
and leaves no half-written row behind.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select

from app.config import Settings
from app.db import get_session
from app.dify_console import DifyConsoleClient
from app.main import create_app
from app.models.agent import AgentKnowledgeBinding, AgentRegistry, AgentToolBinding
from app.models.knowledge import KnowledgeBaseRegistry
from app.models.tenant import Tenant
from app.models.tool import ToolRegistry
from app.models.user import User

# The imports above must precede every other statement; the case identifier
# leads the module body from here on.
TC_ID = "TC-tech-agent-01"

AGENTS_PATH = "/api/agents"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_PEM = (
    _KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)


class Harness:
    """A running app plus the handles a case needs to steer it."""

    def __init__(self, http: httpx.AsyncClient, console: AsyncMock) -> None:
        self.http = http
        self.console = console

    async def close(self) -> None:
        await self.http.aclose()


def _console_mock() -> AsyncMock:
    """A Dify console double: creation succeeds, teardown is recorded."""
    console = AsyncMock(spec=DifyConsoleClient)
    console.create_app.return_value = {"id": "dify-app-1", "name": "app"}
    console.configure_model.return_value = {"result": "success"}
    console.create_api_key.return_value = {"id": "key-1", "token": "app-secret-1"}
    console.delete_app.return_value = None
    return console


async def _start(session_factory, tenant: Tenant, user: User) -> Harness:  # type: ignore[no-untyped-def]
    """Boot the app against the test database, authenticated as ``user``.

    ``POST /api/agents`` provisions the Dify application before it persists the
    agent, so the route needs ``app.state.dify_console``; the double also records
    the compensation call a rejected registration must still make.
    """
    console = _console_mock()
    app = create_app(Settings(_env_file=None, jwt_public_key=_PUBLIC_PEM, rate_limit_enabled=False))
    app.state.dify_console = console

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    token = pyjwt.encode(
        {"sub": str(user.id), "tenantId": str(tenant.id), "role": user.role},
        _KEY,
        algorithm="RS256",
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    http = httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Harness(http, console)


async def _seed_tenant(session_factory, *, name: str) -> Tenant:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant = Tenant(
            name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}", sso_provider="local"
        )
        session.add(tenant)
        await session.commit()
        return tenant


async def _seed_user(session_factory, tenant: Tenant, *, role: str = "agent_admin"):  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        user = User(
            tenant_id=tenant.id,
            sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
            email=f"{uuid.uuid4().hex[:8]}@{tenant.name.lower()}.test",
            name="Admin",
            role=role,
        )
        session.add(user)
        await session.commit()
        return user


async def _seed_kb(session_factory, tenant: Tenant, *, kb_id: str, name: str):  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        kb = KnowledgeBaseRegistry(
            kb_id=kb_id, tenant_id=tenant.id, dify_dataset_id=f"ds-{kb_id}", name=name
        )
        session.add(kb)
        await session.commit()
        return kb


async def _seed_tool(session_factory, *, tool_id: str, name: str, tenant: Tenant | None):  # type: ignore[no-untyped-def]
    """Seed a tool; ``tenant=None`` is the platform-wide (global) tool of C-65."""
    async with session_factory() as session:
        tool = ToolRegistry(
            tool_id=tool_id, tenant_id=None if tenant is None else tenant.id, name=name
        )
        session.add(tool)
        await session.commit()
        return tool


async def _seed_agent(session_factory, tenant: Tenant, user: User, *, agent_id: str):  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        agent = AgentRegistry(
            agent_id=agent_id,
            tenant_id=tenant.id,
            dify_app_id=f"dify-{agent_id}",
            name=agent_id,
            created_by=user.id,
        )
        session.add(agent)
        await session.commit()
        return agent


async def _bind_row(  # type: ignore[no-untyped-def]
    session_factory, *, agent_id: str, kb_id: str | None = None, tool_id: str | None = None
) -> None:
    """Write binding rows directly, reproducing rows a pre-fix deployment stored."""
    async with session_factory() as session:
        if kb_id is not None:
            session.add(AgentKnowledgeBinding(agent_id=agent_id, kb_id=kb_id))
        if tool_id is not None:
            session.add(AgentToolBinding(agent_id=agent_id, tool_id=tool_id))
        await session.commit()


async def _count_rows(session_factory, model, *filters) -> int:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        stmt = select(func.count()).select_from(model).where(*filters)
        return int(await session.scalar(stmt) or 0)


async def _detail(harness: Harness, agent_id: str) -> dict[str, object]:
    """Fetch one agent's detail payload, asserting it is readable."""
    response = await harness.http.get(f"{AGENTS_PATH}/{agent_id}")
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


def _error_code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


# ── write side: a foreign asset is refused as unknown (C-102 / C-14) ────────


@pytest.mark.asyncio
async def test_create_rejects_a_foreign_tenant_knowledge_base(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Another tenant's knowledge base cannot be bound and is not echoed back."""
    acme = await _seed_tenant(session_factory, name="Acme")
    globex = await _seed_tenant(session_factory, name="Globex")
    user = await _seed_user(session_factory, acme)
    await _seed_kb(session_factory, globex, kb_id="kb-globex", name="Globex Handbook")

    harness = await _start(session_factory, acme, user)
    try:
        response = await harness.http.post(
            AGENTS_PATH,
            json={"agentId": "bot-fk", "name": "Bot", "knowledgeBaseIds": ["kb-globex"]},
        )
    finally:
        await harness.close()

    assert response.status_code == 400, response.text
    assert _error_code(response) == "INVALID_BINDING"
    assert "kb-globex" not in response.text and "Globex Handbook" not in response.text
    assert await _count_rows(session_factory, AgentRegistry) == 0
    assert await _count_rows(session_factory, AgentKnowledgeBinding) == 0
    # The Dify app created for the request is compensated, as for any rejected
    # registration, so the failure leaves nothing half-written anywhere.
    harness.console.delete_app.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_rejects_a_foreign_tenant_tool(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Another tenant's tool is refused; this tenant's tool list is untouched."""
    acme = await _seed_tenant(session_factory, name="Acme")
    globex = await _seed_tenant(session_factory, name="Globex")
    user = await _seed_user(session_factory, acme)
    await _seed_tool(session_factory, tool_id="tool-globex", name="Globex CRM", tenant=globex)

    harness = await _start(session_factory, acme, user)
    try:
        response = await harness.http.post(
            AGENTS_PATH,
            json={"agentId": "bot-fk", "name": "Bot", "toolIds": ["tool-globex"]},
        )
    finally:
        await harness.close()

    assert response.status_code == 400, response.text
    assert _error_code(response) == "INVALID_BINDING"
    assert "tool-globex" not in response.text and "Globex CRM" not in response.text
    assert await _count_rows(session_factory, AgentRegistry) == 0
    assert await _count_rows(session_factory, AgentToolBinding) == 0


@pytest.mark.asyncio
async def test_create_accepts_own_assets_and_global_tools(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Narrowing stops at the tenant: own knowledge base and global tool bind."""
    acme = await _seed_tenant(session_factory, name="Acme")
    user = await _seed_user(session_factory, acme)
    await _seed_kb(session_factory, acme, kb_id="kb-acme", name="Acme Handbook")
    await _seed_tool(session_factory, tool_id="tool-shared", name="Shared Search", tenant=None)

    harness = await _start(session_factory, acme, user)
    try:
        created = await harness.http.post(
            AGENTS_PATH,
            json={
                "agentId": "bot-own",
                "name": "Bot",
                "knowledgeBaseIds": ["kb-acme"],
                "toolIds": ["tool-shared"],
            },
        )
        assert created.status_code == 201, created.text
        detail = await _detail(harness, "bot-own")
    finally:
        await harness.close()

    assert detail["boundKnowledge"] == [{"kbId": "kb-acme", "name": "Acme Handbook"}]
    assert detail["boundTools"] == [{"toolId": "tool-shared", "name": "Shared Search"}]


@pytest.mark.asyncio
async def test_update_rejects_a_foreign_binding_and_keeps_the_stored_set(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """A refused replacement writes nothing back, not even the version bump."""
    acme = await _seed_tenant(session_factory, name="Acme")
    globex = await _seed_tenant(session_factory, name="Globex")
    user = await _seed_user(session_factory, acme)
    await _seed_kb(session_factory, acme, kb_id="kb-acme", name="Acme Handbook")
    await _seed_tool(session_factory, tool_id="tool-globex", name="Globex CRM", tenant=globex)
    await _seed_agent(session_factory, acme, user, agent_id="bot-replace")
    await _bind_row(session_factory, agent_id="bot-replace", kb_id="kb-acme")

    harness = await _start(session_factory, acme, user)
    try:
        response = await harness.http.patch(
            f"{AGENTS_PATH}/bot-replace",
            json={
                "version": 1,
                "knowledgeBaseIds": [],
                "toolIds": ["tool-globex"],
            },
        )
        assert response.status_code == 400, response.text
        assert _error_code(response) == "INVALID_BINDING"
        detail = await _detail(harness, "bot-replace")
    finally:
        await harness.close()

    assert detail["version"] == 1
    assert detail["boundKnowledge"] == [{"kbId": "kb-acme", "name": "Acme Handbook"}]
    assert detail["boundTools"] == []
    assert await _count_rows(session_factory, AgentToolBinding) == 0


# ── read side: a stored foreign binding is never serialized (F1) ────────────


@pytest.mark.asyncio
async def test_detail_hides_a_binding_that_points_at_another_tenant(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """Rows a pre-fix deployment stored neither surface nor leak field values."""
    acme = await _seed_tenant(session_factory, name="Acme")
    globex = await _seed_tenant(session_factory, name="Globex")
    user = await _seed_user(session_factory, acme)
    await _seed_kb(session_factory, acme, kb_id="kb-acme", name="Acme Handbook")
    await _seed_kb(session_factory, globex, kb_id="kb-globex", name="Globex Handbook")
    await _seed_tool(session_factory, tool_id="tool-globex", name="Globex CRM", tenant=globex)
    await _seed_tool(session_factory, tool_id="tool-shared", name="Shared Search", tenant=None)
    await _seed_agent(session_factory, acme, user, agent_id="bot-legacy")
    await _bind_row(
        session_factory,
        agent_id="bot-legacy",
        kb_id="kb-globex",
        tool_id="tool-globex",
    )
    await _bind_row(session_factory, agent_id="bot-legacy", kb_id="kb-acme")
    await _bind_row(session_factory, agent_id="bot-legacy", tool_id="tool-shared")

    harness = await _start(session_factory, acme, user)
    try:
        response = await harness.http.get(f"{AGENTS_PATH}/bot-legacy")
    finally:
        await harness.close()

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["boundKnowledge"] == [{"kbId": "kb-acme", "name": "Acme Handbook"}]
    assert body["boundTools"] == [{"toolId": "tool-shared", "name": "Shared Search"}]
    assert "kb-globex" not in response.text and "Globex" not in response.text
