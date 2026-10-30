"""Agent debug turn: the streaming surface of ``POST /api/agents/{id}/debug``.

The debug panel is judged at the HTTP boundary: the frame shape and the terminal
event it closes on, the trace id the frame carries and the run log that persists
it, the role and tenant gates ahead of the stream, and the stateless-turn rules
(no conversation row, no approval gate, no recalled memories).

The Dify Service-API boundary is mocked; every layer below it — event
normalisation, framing, the run-log handler, the repository — is the real
implementation. The browser-side case that drives a real model provider is the
frontend journey ``e2e/journeys/tc-agent-04-agent-debug.spec.ts``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import pytest

import app.events.run_log as run_log_events
import app.services.conversation as conversation_module
from app.core.exceptions import DifyApiError
from app.models.agent import AgentRegistry
from app.models.user_memory import UserMemory

_DEBUG_PATH = "/api/agents/{agent_id}/debug"


def _chunk(event: str, data: dict[str, Any]) -> bytes:
    """One upstream SSE chunk, naming its event inside the JSON payload as Dify does."""
    return f"data: {json.dumps({'event': event, **data})}\n\n".encode()


class _Upstream:
    """Dify Service-API stand-in: a fixed raw SSE stream that records its calls."""

    def __init__(self, frames: list[bytes], *, stream_error: Exception | None = None) -> None:
        self.frames = frames
        self.stream_error = stream_error
        self.bodies: list[dict[str, Any]] = []

    async def post_stream(self, path: str, body: Any) -> AsyncIterator[bytes]:
        self.bodies.append(dict(body))
        for frame in self.frames:
            yield frame
        if self.stream_error is not None:
            raise self.stream_error

    async def aclose(self) -> None:
        pass


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> Callable[[_Upstream], _Upstream]:
    """Install an upstream stub and return it for per-test configuration."""

    def _install(stub: _Upstream) -> _Upstream:
        monkeypatch.setattr(conversation_module, "DifyClientService", lambda *a, **kw: stub)
        return stub

    return _install


def _frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse a platform SSE body into ``(event name, payload)`` pairs.

    Asserts the contracted frame shape on the way: one ``event:`` line followed
    by exactly one single-line ``data:`` JSON line, then a blank separator.
    """
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        if not block:
            continue
        lines = block.split("\n")
        assert len(lines) == 2, f"not an event name plus one data line: {block!r}"
        assert lines[0].startswith("event: "), block
        assert lines[1].startswith("data: "), block
        frames.append((lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :])))
    return frames


def _payload(body: str, event: str) -> dict[str, Any]:
    matches = [data for name, data in _frames(body) if name == event]
    assert len(matches) == 1, f"expected exactly one {event!r} frame in {body!r}"
    return matches[0]


def _trace_id() -> str:
    """A unique trace id per test: run-log trace ids are globally unique (C-103)."""
    return f"debug-trace-{uuid.uuid4().hex[:12]}"


def _turn(trace_id: str, *, chunks: tuple[str, ...] = ("你", "好", "世界")) -> list[bytes]:
    """An upstream turn whose answer arrives as ``chunks``."""
    frames = [
        _chunk(
            "message",
            {"answer": chunks[0], "message_id": "m1", "conversation_id": f"dify-{trace_id}"},
        )
    ]
    frames.extend(_chunk("message", {"answer": chunk, "message_id": "m1"}) for chunk in chunks[1:])
    frames.append(
        _chunk(
            "message_end",
            {
                "id": trace_id,
                "conversation_id": f"dify-{trace_id}",
                "metadata": {"usage": {"total_tokens": 7, "latency": 0.25}},
            },
        )
    )
    return frames


async def _debug(api, headers: dict[str, str], agent_id: str, query: str = "请用一句话介绍你自己"):
    return await api.client.post(
        _DEBUG_PATH.format(agent_id=agent_id), headers=headers, json={"query": query}
    )


# ── the stream ──


@pytest.mark.asyncio
async def test_debug_stream_announces_an_unbuffered_event_stream(api, upstream) -> None:
    # A draft agent is exactly what the debug tab is for: the status gate that
    # hides unpublished agents from conversations must not block debugging.
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="draft-bot", status="draft")
    upstream(_Upstream(_turn(_trace_id())))

    resp = await _debug(api, api.auth(admin, tenant), "draft-bot")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["connection"] == "keep-alive"
    assert resp.headers["x-accel-buffering"] == "no"


@pytest.mark.asyncio
async def test_answer_arrives_as_growing_increments_then_the_end_event(api, upstream) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="chat-bot")
    trace_id = _trace_id()
    upstream(_Upstream(_turn(trace_id)))

    resp = await _debug(api, api.auth(admin, tenant), "chat-bot")

    frames = _frames(resp.text)  # the parser asserts the two-line frame shape
    assert [name for name, _ in frames] == ["message", "message", "message", "message_end"]
    # Each answer frame carries only the increment the model emitted, so the
    # client can render the answer as it grows instead of in one snapshot.
    assert [data["content"] for name, data in frames if name == "message"] == ["你", "好", "世界"]
    # The only normal termination is the end event, and it carries the trace id.
    assert frames[-1][1]["traceId"] == trace_id
    assert "error" not in [name for name, _ in frames]


@pytest.mark.asyncio
async def test_trace_id_is_persisted_as_a_run_log_readable_after_the_stream(
    api, upstream, monkeypatch
) -> None:
    # The writer (C-80) persists through the app-global session factory; point it
    # at the fixture's transaction so the write is readable through the API.
    monkeypatch.setattr(run_log_events, "async_session_factory", api.db.session)

    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="traced-bot")
    trace_id = _trace_id()
    upstream(_Upstream(_turn(trace_id)))
    headers = api.auth(admin, tenant)

    resp = await _debug(api, headers, "traced-bot", query="测试问题")
    assert _payload(resp.text, "message_end")["traceId"] == trace_id

    detail = await api.client.get(f"/api/run-logs/{trace_id}", headers=headers)

    assert detail.status_code == 200, detail.text
    body = detail.json()["data"]
    assert body["traceId"] == trace_id
    assert body["agentId"] == "traced-bot"
    assert body["input"] == "测试问题"
    # C-127: status is reversible from the trace id, even with no workflow status.
    assert body["status"] == "success"
    assert body["tokenUsage"] == 7
    assert body["latencyMs"] == 250


@pytest.mark.asyncio
async def test_a_failed_upstream_ends_with_one_error_frame_and_no_end_event(api, upstream) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="broken-bot")
    upstream(
        _Upstream(
            [_chunk("message", {"answer": "部分", "message_id": "m1"})],
            stream_error=DifyApiError(502, "upstream broke"),
        )
    )

    resp = await _debug(api, api.auth(admin, tenant), "broken-bot")

    names = [name for name, _ in _frames(resp.text)]
    assert names == ["message", "error"]
    assert _payload(resp.text, "error") == {"code": "STREAM_ERROR", "message": "Stream interrupted"}


# ── stateless turn rules ──


@pytest.mark.asyncio
async def test_debug_turn_starts_a_fresh_stateless_upstream_conversation(api, upstream) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="stateless-bot")
    # Both memory types the adapter can inject, held by the caller: with these
    # in place an implemented recall would fill the inputs, so the empty
    # assertions below can actually fail.
    async with api.db.session() as session:
        session.add_all(
            [
                UserMemory(
                    tenant_id=tenant.id,
                    user_id=admin.id,
                    type="preference",
                    content="偏好简短回答",
                    importance=1.0,
                ),
                UserMemory(
                    tenant_id=tenant.id,
                    user_id=admin.id,
                    type="fact",
                    content="负责平台运维",
                    importance=1.0,
                ),
            ]
        )
        await session.commit()
    stub = upstream(_Upstream(_turn(_trace_id())))

    await _debug(api, api.auth(admin, tenant), "stateless-bot", query="hi")

    body = stub.bodies[0]
    assert body["conversation_id"] == ""
    # A debug conversation is never reopened, so the provider must not spend a
    # turn naming it.
    assert body["auto_generate_name"] is False
    # The admin's own memories are not the end user's: none are injected.
    assert body["inputs"]["user_preferences"] == ""
    assert body["inputs"]["user_facts"] == ""


@pytest.mark.asyncio
async def test_gated_tool_call_is_not_turned_into_an_approval_task(api, upstream) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="tool-bot")
    await api.seed_tool(tenant, tool_id="gated-tool", name="gated-tool", risk_level="high")
    upstream(
        _Upstream(
            [
                _chunk(
                    "agent_thought",
                    {
                        "id": "th1",
                        "thought": "calling the gated tool",
                        "tool": "gated-tool",
                        "tool_input": json.dumps({"query": "x"}),
                    },
                ),
                _chunk("message_end", {"id": _trace_id(), "metadata": {}}),
            ]
        )
    )

    resp = await _debug(api, api.auth(admin, tenant), "tool-bot")

    assert [name for name, _ in _frames(resp.text)] == ["agent_thought", "message_end"]
    tasks = await api.client.get("/api/tasks", headers=api.auth(admin, tenant))
    assert tasks.status_code == 200
    assert tasks.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_debug_turn_leaves_no_conversation_behind(api, upstream) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="clean-bot")
    upstream(_Upstream(_turn(_trace_id())))
    headers = api.auth(admin, tenant)

    assert (await _debug(api, headers, "clean-bot")).status_code == 200

    listed = await api.client.get("/api/conversations", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["data"]["items"] == []


# ── gates ahead of the stream ──


@pytest.mark.parametrize("role", ["employee", "knowledge_admin", "auditor"])
@pytest.mark.asyncio
async def test_debug_is_limited_to_the_agent_admin_roles(api, upstream, role: str) -> None:
    tenant, user = await api.seed(role=role)
    await api.seed_agent(tenant, agent_id="guarded-bot")
    stub = upstream(_Upstream(_turn(_trace_id())))

    resp = await _debug(api, api.auth(user, tenant), "guarded-bot")

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "FORBIDDEN"
    # The gate runs before any upstream call.
    assert stub.bodies == []


@pytest.mark.asyncio
async def test_a_platform_admin_may_debug_any_tenant(api, upstream) -> None:
    tenant, _admin = await api.seed(role="agent_admin")
    platform_tenant, platform_admin = await api.seed(role="platform_admin")
    await api.seed_agent(tenant, agent_id="other-bot")
    upstream(_Upstream(_turn(_trace_id())))

    # A platform admin may act on any tenant; the token carries the target one.
    resp = await api.client.post(
        _DEBUG_PATH.format(agent_id="other-bot"),
        headers=api.auth(platform_admin, tenant),
        json={"query": "hi"},
    )

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_an_agent_of_another_tenant_is_response_indistinguishable_from_missing(
    api, upstream
) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    other_tenant, _other_user = await api.seed(role="agent_admin")
    await api.seed_agent(other_tenant, agent_id="foreign-bot")
    stub = upstream(_Upstream(_turn(_trace_id())))

    foreign = await _debug(api, api.auth(admin, tenant), "foreign-bot")
    missing = await _debug(api, api.auth(admin, tenant), "no-such-bot")

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"] == "AGENT_NOT_FOUND"
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]
    assert stub.bodies == []


@pytest.mark.asyncio
async def test_a_soft_deleted_agent_is_not_debuggable(api, upstream) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="deleted-bot")
    async with api.db.session() as session:
        agent = await session.get(AgentRegistry, (tenant.id, "deleted-bot"))
        agent.archived_at = datetime.now(UTC)
        await session.commit()
    stub = upstream(_Upstream(_turn(_trace_id())))

    resp = await _debug(api, api.auth(admin, tenant), "deleted-bot")

    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "AGENT_NOT_FOUND"
    assert stub.bodies == []


@pytest.mark.asyncio
async def test_an_agent_without_its_api_key_cannot_be_debugged(api, upstream) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="keyless-bot")
    async with api.db.session() as session:
        agent = await session.get(AgentRegistry, (tenant.id, "keyless-bot"))
        agent.dify_api_key = None
        await session.commit()
    stub = upstream(_Upstream(_turn(_trace_id())))

    resp = await _debug(api, api.auth(admin, tenant), "keyless-bot")

    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "AGENT_API_KEY_MISSING"
    assert stub.bodies == []


@pytest.mark.parametrize("query", ["", "   "])
@pytest.mark.asyncio
async def test_an_empty_debug_message_is_rejected_before_the_stream(
    api, upstream, query: str
) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="validation-bot")
    stub = upstream(_Upstream(_turn(_trace_id())))

    resp = await _debug(api, api.auth(admin, tenant), "validation-bot", query=query)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert stub.bodies == []


@pytest.mark.asyncio
async def test_an_oversized_debug_message_is_rejected_before_the_stream(api, upstream) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="long-bot")
    stub = upstream(_Upstream(_turn(_trace_id())))

    resp = await _debug(api, api.auth(admin, tenant), "long-bot", query="x" * 10_001)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert stub.bodies == []
