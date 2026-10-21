"""Acceptance case TC-tech-conv-02.

The streaming prepare phase must fail as plain JSON before any stream is
established (a conversation that is not the caller's, or an unusable
identifier, is an indistinguishable 404; a mismatched agent is 422; a missing
agent key is 409), the tool-call confirm surface must not leak the existence of
calls it refuses, a conversation that never exchanged a turn has an empty
message history, and a soft delete can be undone.

Only the Dify boundary is mocked; ownership, the error envelope, the task
transition behind a confirm and the persistence behind history are real.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update

import app.services.conversation as conversation_module
from app.core.exceptions import DifyApiError
from app.models.agent import AgentRegistry
from app.models.conversation import Conversation
from app.models.task import Task

TC_ID = "TC-tech-conv-02"

_CONVERSATIONS = "/api/conversations"
_DETAIL = _CONVERSATIONS + "/{conversation_id}"
_MESSAGES = _DETAIL + "/messages"
_CONFIRM = _CONVERSATIONS + "/{conversation_id}/tool-calls/{call_id}/confirm"


class _UpstreamDify:
    """Dify Service-API stand-in for the message-history path."""

    def __init__(
        self,
        *,
        history: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.history = history if history is not None else []
        self.error = error
        self.calls: list[str] = []

    async def get(
        self, path: str, query: Any = None, *, retries: int = 3, timeout: Any = None
    ) -> dict[str, Any]:
        self.calls.append(path)
        if self.error is not None:
            raise self.error
        return {"data": list(self.history), "has_more": False}

    async def aclose(self) -> None:
        pass


class _CountingFactory:
    """Records whether the service reached out to Dify at all."""

    def __init__(self, upstream: _UpstreamDify) -> None:
        self.upstream = upstream
        self.constructed = 0

    def __call__(self, *args: Any, **kwargs: Any) -> _UpstreamDify:
        self.constructed += 1
        return self.upstream


async def _owned_conversation(api):
    """Seed a tenant/user/agent and open a conversation owned by that user."""
    tenant, user = await api.seed(role="employee")
    await api.seed_agent(tenant, agent_id="agent-1", status="published")
    headers = api.auth(user, tenant)
    created = await api.client.post(_CONVERSATIONS, headers=headers, json={"agentId": "agent-1"})
    assert created.status_code == 201
    return tenant, user, created.json()["data"]["id"], headers


def _send(query: str = "hi", agent_id: str = "agent-1") -> dict[str, str]:
    return {"query": query, "agentId": agent_id}


async def _set_agent_key(api, agent_id: str, api_key: str | None) -> None:
    async with api.db.session() as session:
        await session.execute(
            update(AgentRegistry)
            .where(AgentRegistry.agent_id == agent_id)
            .values(dify_api_key=api_key)
        )
        await session.commit()


async def _record_upstream_conversation(api, conversation_id: str) -> None:
    """Give the conversation an upstream id, as a completed first turn would."""
    async with api.db.session() as session:
        await session.execute(
            update(Conversation)
            .where(Conversation.id == uuid.UUID(conversation_id))
            .values(dify_conversation_id="dify-c1")
        )
        await session.commit()


async def _seed_call(
    api,
    tenant,
    user,
    *,
    conversation_id: str,
    status: str = "pending",
    type_: str = "tool_approval",
    expires_at: datetime | None = None,
) -> str:
    """Create the pending tool-approval task a chat tool call is confirmed through."""
    async with api.db.session() as session:
        task = Task(
            tenant_id=tenant.id,
            creator_id=user.id,
            type=type_,
            title="Approve tool",
            priority="high",
            status=status,
            payload={"conversation_id": str(conversation_id)},
            max_retries=3,
            expires_at=expires_at,
        )
        session.add(task)
        await session.commit()
        return str(task.id)


@pytest.mark.asyncio
async def test_send_message_refuses_foreign_unknown_and_malformed_ids_alike(api) -> None:
    tenant, _user, conversation_id, headers = await _owned_conversation(api)
    intruder = await api.seed_user(tenant)

    refusals = [
        # A conversation that exists but belongs to somebody else.
        await api.client.post(
            _MESSAGES.format(conversation_id=conversation_id),
            headers=api.auth(intruder, tenant),
            json=_send(),
        ),
        # A well-formed identifier that names no conversation.
        await api.client.post(
            _MESSAGES.format(conversation_id=uuid.uuid4()), headers=headers, json=_send()
        ),
        # A malformed identifier.
        await api.client.post(
            _MESSAGES.format(conversation_id="not-a-conversation"), headers=headers, json=_send()
        ),
    ]

    for resp in refusals:
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("application/json")
        # Not one byte of the failure distinguishes a foreign conversation from
        # a missing one.
        assert resp.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_send_message_with_a_mismatched_agent_returns_422(api) -> None:
    tenant, _user, conversation_id, headers = await _owned_conversation(api)
    await api.seed_agent(tenant, agent_id="agent-2", status="published")

    resp = await api.client.post(
        _MESSAGES.format(conversation_id=conversation_id),
        headers=headers,
        json=_send(agent_id="agent-2"),
    )

    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["error"]["code"] == "AGENT_MISMATCH"


@pytest.mark.asyncio
async def test_send_message_without_an_agent_key_returns_409(api) -> None:
    _tenant, _user, conversation_id, headers = await _owned_conversation(api)
    await _set_agent_key(api, "agent-1", None)

    resp = await api.client.post(
        _MESSAGES.format(conversation_id=conversation_id), headers=headers, json=_send()
    )

    assert resp.status_code == 409
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["error"]["code"] == "AGENT_API_KEY_MISSING"


@pytest.mark.asyncio
async def test_a_non_admin_cannot_open_a_conversation_with_an_unpublished_agent(api) -> None:
    tenant, employee = await api.seed(role="employee")
    admin = await api.seed_user(tenant, role="agent_admin")
    await api.seed_agent(tenant, agent_id="agent-draft", status="draft")

    refused = await api.client.post(
        _CONVERSATIONS, headers=api.auth(employee, tenant), json={"agentId": "agent-draft"}
    )
    allowed = await api.client.post(
        _CONVERSATIONS, headers=api.auth(admin, tenant), json={"agentId": "agent-draft"}
    )

    assert refused.status_code == 404
    assert refused.json()["error"]["code"] == "AGENT_NOT_FOUND"
    assert allowed.status_code == 201


@pytest.mark.asyncio
async def test_message_history_is_an_empty_page_before_any_turn(api, monkeypatch) -> None:
    factory = _CountingFactory(_UpstreamDify())
    monkeypatch.setattr(conversation_module, "DifyClientService", factory)
    _tenant, _user, conversation_id, headers = await _owned_conversation(api)

    resp = await api.client.get(_MESSAGES.format(conversation_id=conversation_id), headers=headers)

    assert resp.status_code == 200
    assert resp.json()["data"] == {"items": [], "nextCursor": None}
    # No upstream conversation exists yet, so history must not be fetched.
    assert factory.constructed == 0


@pytest.mark.asyncio
async def test_message_history_returns_502_when_upstream_fails(api, monkeypatch) -> None:
    upstream = _UpstreamDify(error=DifyApiError(500, "history unavailable"))
    monkeypatch.setattr(conversation_module, "DifyClientService", lambda *a, **kw: upstream)
    _tenant, _user, conversation_id, headers = await _owned_conversation(api)
    await _record_upstream_conversation(api, conversation_id)

    resp = await api.client.get(_MESSAGES.format(conversation_id=conversation_id), headers=headers)

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "DIFY_ERROR"


@pytest.mark.asyncio
async def test_message_history_returns_409_when_the_agent_has_no_key(api, monkeypatch) -> None:
    factory = _CountingFactory(_UpstreamDify())
    monkeypatch.setattr(conversation_module, "DifyClientService", factory)
    _tenant, _user, conversation_id, headers = await _owned_conversation(api)
    await _record_upstream_conversation(api, conversation_id)
    await _set_agent_key(api, "agent-1", None)

    resp = await api.client.get(_MESSAGES.format(conversation_id=conversation_id), headers=headers)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "AGENT_API_KEY_MISSING"
    assert factory.constructed == 0


@pytest.mark.asyncio
async def test_confirm_refuses_every_call_that_is_not_a_pending_call_of_this_conversation(
    api,
) -> None:
    tenant, user, conversation_id, headers = await _owned_conversation(api)
    other = await api.client.post(_CONVERSATIONS, headers=headers, json={"agentId": "agent-1"})
    assert other.status_code == 201
    other_conversation_id = other.json()["data"]["id"]

    refusals = {
        # An identifier that was never issued.
        "unknown": str(uuid.uuid4()),
        # A malformed identifier.
        "malformed": "not-a-call",
        # A pending call belonging to another conversation of the same user.
        "other_conversation": await _seed_call(
            api, tenant, user, conversation_id=other_conversation_id
        ),
        # A task that is not a tool approval at all.
        "not_a_tool_call": await _seed_call(
            api, tenant, user, conversation_id=conversation_id, type_="tool_execution"
        ),
        # A call that was already resolved.
        "already_resolved": await _seed_call(
            api, tenant, user, conversation_id=conversation_id, status="completed"
        ),
        # A call whose approval window has passed.
        "expired": await _seed_call(
            api,
            tenant,
            user,
            conversation_id=conversation_id,
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
        ),
    }

    for case, call_id in refusals.items():
        resp = await api.client.post(
            _CONFIRM.format(conversation_id=conversation_id, call_id=call_id), headers=headers
        )
        assert resp.status_code == 404, case
        assert resp.headers["content-type"].startswith("application/json"), case
        # Refusals never disclose whether the call exists.
        assert resp.json()["error"]["code"] == "NOT_FOUND", case


@pytest.mark.asyncio
async def test_confirm_of_a_pending_call_approves_it(api) -> None:
    tenant, user, conversation_id, headers = await _owned_conversation(api)
    call_id = await _seed_call(api, tenant, user, conversation_id=conversation_id)

    resp = await api.client.post(
        _CONFIRM.format(conversation_id=conversation_id, call_id=call_id), headers=headers
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {"callId": call_id, "status": "approved"}

    # The confirmation runs the approval path, so the call reads back approved.
    read_back = await api.client.get(f"/api/tasks/{call_id}", headers=headers)
    assert read_back.status_code == 200
    assert read_back.json()["data"]["status"] == "approved"


@pytest.mark.asyncio
async def test_undoing_a_soft_delete_makes_the_conversation_readable_again(api) -> None:
    _tenant, _user, conversation_id, headers = await _owned_conversation(api)
    detail = _DETAIL.format(conversation_id=conversation_id)
    undo = _CONVERSATIONS + f"/{conversation_id}/restore"

    deleted = await api.client.delete(detail, headers=headers)
    assert deleted.status_code == 204
    assert (await api.client.get(detail, headers=headers)).status_code == 404

    restored = await api.client.post(undo, headers=headers)
    assert restored.status_code == 200
    assert (await api.client.get(detail, headers=headers)).status_code == 200

    # A second undo has nothing left to undo: the conversation is live again.
    # Asserting it only here keeps the 404 meaningful — the same endpoint just
    # answered 200, so a 404 means "no deletion to undo", not "no such route".
    assert (await api.client.post(undo, headers=headers)).status_code == 404
