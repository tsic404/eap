"""SSE fault-tolerance scenarios (architecture doc §31.1.5 / §33.7.6).

Five scenarios map onto the backend's fault-tolerance surface: normal stream,
upstream failure (a Dify disconnect *or* the 120s ``STREAM_TIMEOUT_SECONDS``
read timeout both raise ``DifyApiError`` → ``error`` frame), ping keepalive
tolerance, truncated-chunk skip, and replayed ``message_id`` → ``replace``. The
frontend reconnect path (§33.7.6 ``e2e/fixtures/sse_proxy.py``) is out of scope
here; Dify stays mocked.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.exceptions import DifyApiError
from app.services.conversation import sse_frame
from app.services.dify_conversation_adapter import DifyConversationAdapter


class _FakeStreamService:
    """Stand-in conversation service emitting a fixed frame sequence."""

    def __init__(self, frames: list[dict[str, Any]], *, exc: Exception | None = None) -> None:
        self._frames = frames
        self._exc = exc

    async def prepare_stream(
        self, session: Any, user: Any, conversation_id: str, agent_id: str
    ) -> tuple[None, None]:
        return None, None

    async def stream(self, session: Any, conversation: Any, agent: Any, user: Any, dto: Any):
        for frame in self._frames:
            yield sse_frame(frame["event"], frame["data"])
        if self._exc is not None:
            raise self._exc


class _FakeMemory:
    async def recall(self, user_id: str, tenant_id: str, query: str) -> list[Any]:
        return []


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event_name: str, payload: dict[str, Any]) -> None:
        self.published.append((event_name, payload))


class _FakeDifyStream:
    """Yields a fixed sequence of raw SSE byte chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def post_stream(self, path: str, body: Any):
        for chunk in self._chunks:
            yield chunk


async def _collect(adapter: DifyConversationAdapter) -> list[dict[str, Any]]:
    return [event async for event in adapter.stream_messages("", "hi", "u1", "t1")]


# ── endpoint-level scenarios ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sse_normal_stream_emits_message_then_end(api) -> None:
    api.app.state.conversation_service = _FakeStreamService(
        [
            {"event": "message", "data": {"content": "hi", "messageId": "m1"}},
            {"event": "message_end", "data": {"traceId": "t1", "metadata": {}}},
        ]
    )
    tenant, user = await api.seed(role="employee")
    await api.seed_agent(tenant, agent_id="agent-1", status="published")

    resp = await api.client.post(
        "/api/conversations/00000000-0000-0000-0000-000000000000/messages",
        headers=api.auth(user, tenant),
        json={"query": "hi", "agentId": "agent-1"},
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert body.index("event: message\n") < body.index("event: message_end\n")


@pytest.mark.asyncio
async def test_sse_midstream_disconnect_emits_error_frame(api) -> None:
    api.app.state.conversation_service = _FakeStreamService(
        [{"event": "message", "data": {"content": "hi", "messageId": "m1"}}],
        exc=DifyApiError(504, "upstream closed"),
    )
    tenant, user = await api.seed(role="employee")
    await api.seed_agent(tenant, agent_id="agent-1", status="published")

    resp = await api.client.post(
        "/api/conversations/00000000-0000-0000-0000-000000000000/messages",
        headers=api.auth(user, tenant),
        json={"query": "hi", "agentId": "agent-1"},
    )

    assert resp.status_code == 200  # stream already started; error is an in-band frame
    assert "event: error\n" in resp.text
    assert '"code":"STREAM_ERROR"' in resp.text


# ── adapter-level scenarios ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sse_ping_keepalive_is_tolerated() -> None:
    adapter = DifyConversationAdapter(
        _FakeDifyStream(
            [
                b'data: {"event":"ping"}\n\n',
                b'data: {"event":"message_end","id":"t1","metadata":{}}\n\n',
            ]
        ),
        _FakeMemory(),
        _FakeBus(),
    )

    events = await _collect(adapter)

    # The keepalive is dropped; the stream still terminates on message_end.
    assert [e["event"] for e in events] == ["message_end"]


@pytest.mark.asyncio
async def test_sse_malformed_chunk_is_skipped() -> None:
    adapter = DifyConversationAdapter(
        _FakeDifyStream(
            [
                b'data: {"event":"message","answer":"ok","message_id":"m1"}\n\n',
                b'data: {"event":"message","answer":"broken\n\n',
                b'data: {"event":"message","answer":"after","message_id":"m2"}\n\n',
            ]
        ),
        _FakeMemory(),
        _FakeBus(),
    )

    events = await _collect(adapter)

    # The truncated JSON line is dropped; the surrounding events still flow.
    assert [e["event"] for e in events] == ["message", "message"]
    assert events[0]["data"]["messageId"] == "m1"
    assert events[1]["data"]["messageId"] == "m2"


@pytest.mark.asyncio
async def test_sse_duplicate_message_id_maps_to_replace() -> None:
    adapter = DifyConversationAdapter(
        _FakeDifyStream(
            [
                b'data: {"event":"message","answer":"partial","message_id":"m1"}\n\n',
                b'data: {"event":"message_replace","answer":"full","message_id":"m1"}\n\n',
            ]
        ),
        _FakeMemory(),
        _FakeBus(),
    )

    events = await _collect(adapter)

    # A replayed message_id arrives as a `replace` so the client swaps, never
    # appends a duplicate bubble.
    assert [e["event"] for e in events] == ["message", "replace"]
    assert events[1]["data"]["messageId"] == "m1"
    assert events[1]["data"]["answer"] == "full"
