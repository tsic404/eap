"""ConversationController HTTP tests: envelope, isolation, delete, SSE streaming."""

from __future__ import annotations

import uuid
from typing import Any

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models.agent import AgentRegistry
from app.models.tenant import Tenant
from app.models.user import User
from app.services.conversation import ConversationService, sse_frame


def _make_tenant_user(*, role: str = "employee") -> tuple[Tenant, User]:
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        sso_provider="local",
        status="active",
    )
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        sso_sub="test-sub",
        email="user@example.com",
        name="User",
        role=role,
        status="active",
    )
    return tenant, user


def _agent(tenant: Tenant, *, agent_id: str = "agent-1") -> AgentRegistry:
    return AgentRegistry(
        agent_id=agent_id,
        tenant_id=tenant.id,
        dify_app_id="dify-app-1",
        dify_api_key="app-secret",
        name="Agent",
        type="chat",
        status="published",
        version=1,
    )


def _keypair() -> tuple[rsa.RSAPrivateKey, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_key, public_pem


def _sign(private_key: rsa.RSAPrivateKey, user: User, tenant: Tenant) -> str:
    return pyjwt.encode(
        {"sub": str(user.id), "tenantId": str(tenant.id), "role": user.role},
        private_key,
        algorithm="RS256",
    )


class _FakeStreamService:
    """Stand-in that ignores CRUD and emits a fixed SSE sequence."""

    async def prepare_stream(
        self, session: Any, user: Any, conversation_id: str, agent_id: str
    ) -> tuple[None, None]:
        return None, None

    async def stream(self, session: Any, conversation: Any, agent: Any, user: Any, dto: Any):
        yield sse_frame("message", {"content": "hi", "messageId": "m1", "conversationId": "c1"})
        yield sse_frame("message_end", {"traceId": "t1", "metadata": {}})


async def _route_client(
    session_factory,
    *,
    role: str = "employee",
    service: Any = None,  # type: ignore[no-untyped-def]
) -> tuple[AsyncClient, rsa.RSAPrivateKey, Tenant, User]:
    private_key, public_pem = _keypair()
    tenant, user = _make_tenant_user(role=role)
    async with session_factory() as session:
        session.add(tenant)
        session.add(user)
        session.add(_agent(tenant))
        await session.commit()

    app = create_app(Settings(_env_file=None, jwt_public_key=public_pem, rate_limit_enabled=False))
    app.state.conversation_service = service or ConversationService(Settings(_env_file=None))

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test"), private_key, tenant, user


@pytest.mark.asyncio
async def test_create_conversation_returns_201(session_factory) -> None:  # type: ignore[no-untyped-def]
    client, private_key, tenant, user = await _route_client(session_factory)
    token = _sign(private_key, user, tenant)

    resp = await client.post(
        "/api/conversations",
        headers={"Authorization": f"Bearer {token}"},
        json={"agentId": "agent-1", "title": "First"},
    )

    assert resp.status_code == 201
    body = resp.json()["data"]
    assert body["agentId"] == "agent-1"
    assert body["agentName"] == "Agent"
    assert body["title"] == "First"
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_list_conversations_isolated_by_user(session_factory) -> None:  # type: ignore[no-untyped-def]
    client, private_key, tenant, alice = await _route_client(session_factory)
    bob = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        sso_sub="other-sub",
        email="other@example.com",
        name="Other",
        role="employee",
        status="active",
    )
    async with session_factory() as session:
        session.add(bob)
        await session.commit()

    alice_token = _sign(private_key, alice, tenant)
    await client.post(
        "/api/conversations",
        headers={"Authorization": f"Bearer {alice_token}"},
        json={"agentId": "agent-1"},
    )

    bob_token = _sign(private_key, bob, tenant)
    resp = await client.get("/api/conversations", headers={"Authorization": f"Bearer {bob_token}"})

    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_delete_conversation_removes_from_list(session_factory) -> None:  # type: ignore[no-untyped-def]
    client, private_key, tenant, user = await _route_client(session_factory)
    token = _sign(private_key, user, tenant)

    created = await client.post(
        "/api/conversations",
        headers={"Authorization": f"Bearer {token}"},
        json={"agentId": "agent-1"},
    )
    conv_id = created.json()["data"]["id"]

    deleted = await client.delete(
        f"/api/conversations/{conv_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert deleted.status_code == 204

    listed = await client.get("/api/conversations", headers={"Authorization": f"Bearer {token}"})
    assert listed.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_send_message_returns_event_stream(session_factory) -> None:  # type: ignore[no-untyped-def]
    client, private_key, tenant, user = await _route_client(
        session_factory, service=_FakeStreamService()
    )
    token = _sign(private_key, user, tenant)

    resp = await client.post(
        "/api/conversations/00000000-0000-0000-0000-000000000000/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "hi", "agentId": "agent-1"},
    )

    # The fake service ignores the conversation id, so the unknown id is fine;
    # what matters is the stream content-type and the message→message_end order.
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    message_at = body.index("event: message\n")
    end_at = body.index("event: message_end\n")
    assert message_at < end_at


@pytest.mark.asyncio
async def test_send_message_validation_error_returns_json(session_factory) -> None:  # type: ignore[no-untyped-def]
    client, private_key, tenant, user = await _route_client(session_factory)
    token = _sign(private_key, user, tenant)

    resp = await client.post(
        "/api/conversations/00000000-0000-0000-0000-000000000000/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "", "agentId": "agent-1"},
    )

    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_send_message_file_without_id_returns_json_422(session_factory) -> None:  # type: ignore[no-untyped-def]
    client, private_key, tenant, user = await _route_client(session_factory)
    token = _sign(private_key, user, tenant)

    resp = await client.post(
        "/api/conversations/00000000-0000-0000-0000-000000000000/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "hi", "agentId": "agent-1", "files": [{"type": "document"}]},
    )

    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
