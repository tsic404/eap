"""Acceptance case TC-tech-conv-01.

A streaming turn answers with an unbuffered event stream whose frames are an
event name plus one line of JSON, terminates normally only through the end
event (trace id, metadata, long-conversation notice flag), drops upstream
keepalives and unknown events, relays the whole event catalogue under its
platform names, truncates citation excerpts to two hundred characters,
terminates an interrupted stream with a single error event and no end event,
and flags the long conversation from its thirty-first turn onwards.

The upstream Dify boundary is mocked by swapping the service's client factory;
everything below it (SSE normalisation, framing, routers, envelope) is real.
"""

import json
from typing import Any

import pytest

import app.services.conversation as conversation_module
from app.core.exceptions import DifyApiError

TC_ID = "TC-tech-conv-01"

_MESSAGES_PATH = "/api/conversations/{conversation_id}/messages"


def _chunk(event: str, data: dict[str, Any]) -> bytes:
    """One upstream SSE chunk, naming its event inside the JSON payload as Dify does."""
    return f"data: {json.dumps({'event': event, **data})}\n\n".encode()


class _Upstream:
    """Dify Service-API stand-in: a fixed raw SSE stream plus a history page."""

    def __init__(
        self,
        frames: list[bytes],
        *,
        history: list[dict[str, Any]] | None = None,
        has_more: bool = False,
        history_error: Exception | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self.frames = frames
        self.history = history if history is not None else []
        self.has_more = has_more
        self.history_error = history_error
        self.stream_error = stream_error

    async def get(
        self, path: str, query: Any = None, *, retries: int = 3, timeout: Any = None
    ) -> dict[str, Any]:
        if self.history_error is not None:
            raise self.history_error
        return {"data": list(self.history), "has_more": self.has_more}

    async def post_stream(self, path: str, body: Any):
        for frame in self.frames:
            yield frame
        if self.stream_error is not None:
            raise self.stream_error

    async def aclose(self) -> None:
        pass


def _install_upstream(monkeypatch: pytest.MonkeyPatch, upstream: _Upstream) -> None:
    """Point the conversation service at ``upstream`` (the mocked Dify boundary)."""
    monkeypatch.setattr(conversation_module, "DifyClientService", lambda *a, **kw: upstream)


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


def _names(body: str) -> list[str]:
    return [name for name, _ in _frames(body)]


def _payload(body: str, event: str) -> dict[str, Any]:
    """The single payload carried by the named event in a platform SSE body."""
    matches = [data for name, data in _frames(body) if name == event]
    assert len(matches) == 1, f"expected exactly one {event!r} frame in {body!r}"
    return matches[0]


def _notice_flag(body: str) -> bool:
    flag = _payload(body, "message_end")["metadata"]["truncationNotice"]
    assert isinstance(flag, bool), f"truncationNotice must be a boolean, got {flag!r}"
    return flag


async def _conversation(api) -> tuple[Any, str, dict[str, str]]:
    """Seed a tenant/user/agent and open a conversation owned by that user."""
    tenant, user = await api.seed(role="employee")
    await api.seed_agent(tenant, agent_id="agent-1", status="published")
    headers = api.auth(user, tenant)
    created = await api.client.post(
        "/api/conversations", headers=headers, json={"agentId": "agent-1"}
    )
    assert created.status_code == 201
    return tenant, created.json()["data"]["id"], headers


async def _stream(api, headers: dict[str, str], conversation_id: str):
    return await api.client.post(
        _MESSAGES_PATH.format(conversation_id=conversation_id),
        headers=headers,
        json={"query": "hi", "agentId": "agent-1"},
    )


async def _after_first_turn(api, monkeypatch, upstream: _Upstream) -> tuple[str, dict[str, str]]:
    """Install ``upstream`` and return a conversation that already ran one turn."""
    _install_upstream(monkeypatch, upstream)
    _, conversation_id, headers = await _conversation(api)
    assert (await _stream(api, headers, conversation_id)).status_code == 200
    return conversation_id, headers


def _turn_frames() -> list[bytes]:
    return [
        _chunk("message", {"answer": "hi", "message_id": "m1", "conversation_id": "c1"}),
        _chunk("message_end", {"id": "t1", "metadata": {}}),
    ]


# The whole upstream-to-platform event catalogue (C-59): one upstream frame, the
# platform event it must be relayed as, and the payload facts that must survive
# the mapping. Keepalives and unknown events are deliberately absent — C-56
# drops them (covered by their own case).
_CATALOG: list[tuple[str, dict[str, Any], str, dict[str, Any]]] = [
    (
        "message",
        {"answer": "hi", "message_id": "m1", "conversation_id": "c1"},
        "message",
        {"content": "hi", "messageId": "m1"},
    ),
    (
        "message_file",
        {"id": "f1", "type": "image", "url": "http://files/1.png", "belongs_to": "user"},
        "file",
        {"id": "f1", "url": "http://files/1.png"},
    ),
    (
        "message_replace",
        {"answer": "replaced", "message_id": "m1"},
        "replace",
        {"answer": "replaced"},
    ),
    (
        "agent_message",
        {"answer": "agent says", "message_id": "m2"},
        "agent_message",
        {"answer": "agent says"},
    ),
    (
        "agent_thought",
        {"id": "th1", "thought": "checking", "tool": "unregistered-tool"},
        "agent_thought",
        {"thought": "checking", "tool": "unregistered-tool"},
    ),
    ("workflow_started", {"workflow_run_id": "wf1"}, "workflow_started", {"workflowRunId": "wf1"}),
    (
        "workflow_finished",
        {"data": {"status": "succeeded", "outputs": {"answer": "done"}}},
        "workflow_finished",
        {"status": "succeeded", "outputs": {"answer": "done"}},
    ),
    (
        "node_started",
        {"data": {"node_id": "n1", "title": "Search", "index": 1}},
        "node_started",
        {"nodeId": "n1", "title": "Search"},
    ),
    (
        "node_finished",
        {"data": {"node_id": "n1", "status": "succeeded"}},
        "node_finished",
        {"nodeId": "n1", "status": "succeeded"},
    ),
    (
        "parallel_branch_started",
        {"data": {"parallel_id": "p1", "parallel_branch_id": "b1"}},
        "branch_started",
        {"parallelId": "p1", "parallelBranchId": "b1"},
    ),
    (
        "parallel_branch_finished",
        {"data": {"status": "succeeded"}},
        "branch_finished",
        {"status": "succeeded"},
    ),
    ("text_chunk", {"data": {"text": "chunk"}}, "text_chunk", {"text": "chunk"}),
    ("text_replace", {"data": {"text": "rewritten"}}, "text_replace", {"text": "rewritten"}),
    (
        "tts_message",
        {"audio": "AAA", "message_id": "m1"},
        "tts_message",
        {"audio": "AAA"},
    ),
    ("tts_message_end", {"audio": "ZZZ"}, "tts_message_end", {"audio": "ZZZ"}),
]

_CATALOG_IDS = [case[0] for case in _CATALOG]


@pytest.mark.asyncio
async def test_stream_headers_announce_an_unbuffered_event_stream(api, monkeypatch) -> None:
    upstream = _Upstream([_chunk("message_end", {"id": "t1", "metadata": {}})])
    _install_upstream(monkeypatch, upstream)
    _, conversation_id, headers = await _conversation(api)

    resp = await _stream(api, headers, conversation_id)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["connection"] == "keep-alive"
    assert resp.headers["x-accel-buffering"] == "no"


@pytest.mark.asyncio
async def test_frames_are_an_event_name_plus_one_line_of_json(api, monkeypatch) -> None:
    _install_upstream(monkeypatch, _Upstream(_turn_frames()))
    _, conversation_id, headers = await _conversation(api)

    resp = await _stream(api, headers, conversation_id)

    frames = _frames(resp.text)  # the parser asserts the two-line frame shape
    assert [name for name, _ in frames] == ["message", "message_end"]
    assert frames[0][1]["content"] == "hi"
    assert frames[0][1]["messageId"] == "m1"


@pytest.mark.parametrize(
    ("upstream_event", "upstream_data", "platform_event", "expected"), _CATALOG, ids=_CATALOG_IDS
)
@pytest.mark.asyncio
async def test_catalogued_upstream_events_are_relayed_under_their_platform_name(
    api,
    monkeypatch,
    upstream_event: str,
    upstream_data: dict[str, Any],
    platform_event: str,
    expected: dict[str, Any],
) -> None:
    _install_upstream(
        monkeypatch,
        _Upstream(
            [
                _chunk(upstream_event, upstream_data),
                _chunk("message_end", {"id": "t1", "metadata": {}}),
            ]
        ),
    )
    _, conversation_id, headers = await _conversation(api)

    resp = await _stream(api, headers, conversation_id)

    assert _names(resp.text) == [platform_event, "message_end"]
    relayed = _payload(resp.text, platform_event)
    assert {key: relayed.get(key) for key in expected} == expected


@pytest.mark.asyncio
async def test_a_gated_tool_call_asks_for_approval_right_after_its_thought(
    api, monkeypatch
) -> None:
    tenant, user = await api.seed(role="employee")
    await api.seed_agent(tenant, agent_id="agent-1", status="published")
    await api.seed_tool(tenant, tool_id="gated-tool", name="gated-tool", risk_level="high")
    _install_upstream(
        monkeypatch,
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
                _chunk("message_end", {"id": "t1", "metadata": {}}),
            ]
        ),
    )
    headers = api.auth(user, tenant)
    created = await api.client.post(
        "/api/conversations", headers=headers, json={"agentId": "agent-1"}
    )
    assert created.status_code == 201
    conversation_id = created.json()["data"]["id"]

    resp = await _stream(api, headers, conversation_id)

    # The approval request follows the thought that triggered it, and carries
    # the task id, tool name and parameters (C-59).
    assert _names(resp.text) == ["agent_thought", "tool_approval_required", "message_end"]
    approval = _payload(resp.text, "tool_approval_required")
    assert approval["toolName"] == "gated-tool"
    assert approval["params"] == {"query": "x"}
    # ...naming a task that really exists and is waiting for approval.
    task = await api.client.get(f"/api/tasks/{approval['taskId']}", headers=headers)
    assert task.status_code == 200
    assert task.json()["data"]["status"] == "pending"


@pytest.mark.asyncio
async def test_the_end_event_is_the_only_normal_termination(api, monkeypatch) -> None:
    _install_upstream(
        monkeypatch,
        _Upstream(
            [
                _chunk("message", {"answer": "hi", "message_id": "m1", "conversation_id": "c1"}),
                _chunk(
                    "message_end",
                    {
                        "id": "trace-1",
                        "metadata": {
                            "usage": {"tokens": 12},
                            "retriever_resources": [
                                {
                                    "document_name": "handbook.md",
                                    "dataset_name": "kb-1",
                                    "content": "excerpt",
                                    "score": 0.9,
                                }
                            ],
                        },
                    },
                ),
            ]
        ),
    )
    _, conversation_id, headers = await _conversation(api)

    resp = await _stream(api, headers, conversation_id)

    frames = _frames(resp.text)
    assert _names(resp.text).count("message_end") == 1
    assert frames[-1][0] == "message_end"
    assert "error" not in _names(resp.text)
    end = frames[-1][1]
    assert end["traceId"] == "trace-1"
    assert end["metadata"]["usage"] == {"tokens": 12}
    assert end["metadata"]["retrieverResources"] == [
        {"sourceName": "handbook.md", "kbName": "kb-1", "excerpt": "excerpt", "score": 0.9}
    ]
    assert _notice_flag(resp.text) is False


@pytest.mark.asyncio
async def test_citation_excerpts_are_truncated_to_two_hundred_characters(api, monkeypatch) -> None:
    long_content = "x" * 250
    _install_upstream(
        monkeypatch,
        _Upstream(
            [
                _chunk("message", {"answer": "hi", "message_id": "m1", "conversation_id": "c1"}),
                _chunk(
                    "message_end",
                    {
                        "id": "t1",
                        "metadata": {
                            "retriever_resources": [
                                {
                                    "document_name": "long.md",
                                    "dataset_name": "kb-1",
                                    "content": long_content,
                                    "score": 0.9,
                                },
                                {
                                    "document_name": "boundary.md",
                                    "dataset_name": "kb-1",
                                    "content": "y" * 200,
                                    "score": 0.5,
                                },
                            ]
                        },
                    },
                ),
            ]
        ),
    )
    _, conversation_id, headers = await _conversation(api)

    resp = await _stream(api, headers, conversation_id)

    resources = _payload(resp.text, "message_end")["metadata"]["retrieverResources"]
    truncated, at_the_cap = resources[0]["excerpt"], resources[1]["excerpt"]
    # The cap is two hundred characters, and it is the document's leading text
    # that survives: the excerpt is that many characters, not a shorter slice.
    assert len(truncated) == 200
    assert long_content.startswith(truncated)
    # An excerpt exactly at the cap is inside it and must survive whole.
    assert at_the_cap == "y" * 200


@pytest.mark.asyncio
async def test_upstream_keepalives_and_unknown_events_are_not_relayed(api, monkeypatch) -> None:
    _install_upstream(
        monkeypatch,
        _Upstream(
            [
                _chunk("ping", {}),
                _chunk("message", {"answer": "hi", "message_id": "m1", "conversation_id": "c1"}),
                _chunk("a_future_upstream_event", {"whatever": 1}),
                _chunk("ping", {}),
                _chunk("message_end", {"id": "t1", "metadata": {}}),
            ]
        ),
    )
    _, conversation_id, headers = await _conversation(api)

    resp = await _stream(api, headers, conversation_id)

    assert _names(resp.text) == ["message", "message_end"]
    assert "ping" not in resp.text
    assert "a_future_upstream_event" not in resp.text


@pytest.mark.parametrize(
    "failure",
    [DifyApiError(504, "upstream closed"), RuntimeError("boom")],
    ids=["upstream_error", "unhandled_exception"],
)
@pytest.mark.asyncio
async def test_an_interrupted_stream_ends_with_one_error_event_and_no_end_event(
    api, monkeypatch, failure: Exception
) -> None:
    _install_upstream(
        monkeypatch,
        _Upstream(
            [_chunk("message", {"answer": "hi", "message_id": "m1", "conversation_id": "c1"})],
            stream_error=failure,
        ),
    )
    _, conversation_id, headers = await _conversation(api)

    resp = await _stream(api, headers, conversation_id)

    assert resp.status_code == 200  # the stream had already started
    assert _names(resp.text) == ["message", "error"]
    error = _payload(resp.text, "error")
    assert error["code"] == "STREAM_ERROR"
    assert error["message"] == "Stream interrupted"


@pytest.mark.asyncio
async def test_an_upstream_error_event_is_relayed_with_its_own_code(api, monkeypatch) -> None:
    _install_upstream(
        monkeypatch,
        _Upstream(
            [
                _chunk(
                    "error",
                    {"code": "upstream_quota_exceeded", "message": "quota", "status": 429},
                )
            ]
        ),
    )
    _, conversation_id, headers = await _conversation(api)

    resp = await _stream(api, headers, conversation_id)

    assert _names(resp.text) == ["error"]
    error = _payload(resp.text, "error")
    assert error["code"] == "upstream_quota_exceeded"
    assert error["status"] == 429


@pytest.mark.asyncio
async def test_the_stream_ends_after_an_upstream_error_event(api, monkeypatch) -> None:
    # The upstream keeps talking after its error frame, so a platform that only
    # stops on the exception path would relay everything that follows.
    _install_upstream(
        monkeypatch,
        _Upstream(
            [
                _chunk(
                    "error",
                    {"code": "upstream_quota_exceeded", "message": "quota", "status": 429},
                ),
                _chunk("message", {"answer": "after", "message_id": "m9", "conversation_id": "c1"}),
                _chunk("message_end", {"id": "t9", "metadata": {}}),
            ]
        ),
    )
    _, conversation_id, headers = await _conversation(api)

    resp = await _stream(api, headers, conversation_id)

    assert _names(resp.text) == ["error"]


@pytest.mark.asyncio
async def test_the_notice_flag_is_false_below_the_threshold_and_true_from_turn_31(
    api, monkeypatch
) -> None:
    upstream = _Upstream(_turn_frames())
    conversation_id, headers = await _after_first_turn(api, monkeypatch, upstream)

    upstream.history = [{"id": f"h{index}"} for index in range(29)]
    assert _notice_flag((await _stream(api, headers, conversation_id)).text) is False

    upstream.history = [{"id": f"h{index}"} for index in range(30)]
    assert _notice_flag((await _stream(api, headers, conversation_id)).text) is True


@pytest.mark.asyncio
async def test_the_notice_flag_is_true_when_upstream_reports_more_history(api, monkeypatch) -> None:
    upstream = _Upstream(_turn_frames(), history=[{"id": "h0"}], has_more=True)
    conversation_id, headers = await _after_first_turn(api, monkeypatch, upstream)

    assert _notice_flag((await _stream(api, headers, conversation_id)).text) is True


@pytest.mark.asyncio
async def test_the_notice_flag_stays_false_when_the_threshold_precheck_fails(
    api, monkeypatch
) -> None:
    upstream = _Upstream(_turn_frames(), history_error=DifyApiError(500, "history unavailable"))
    conversation_id, headers = await _after_first_turn(api, monkeypatch, upstream)

    assert _notice_flag((await _stream(api, headers, conversation_id)).text) is False
