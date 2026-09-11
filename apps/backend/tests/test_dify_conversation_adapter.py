"""DifyConversationAdapter unit tests (fakes for Dify client / memory / event bus)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.services.dify_conversation_adapter import DifyConversationAdapter


class FakeDifyClient:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.stream_calls: list[tuple[str, dict[str, Any]]] = []

    async def post_stream(self, path: str, body: dict[str, Any]) -> AsyncIterator[bytes]:
        self.stream_calls.append((path, body))
        for chunk in self._chunks:
            yield chunk


class FakeMemory:
    def __init__(self, memories: list[dict[str, Any]]) -> None:
        self._memories = memories
        self.recall_calls: list[tuple[str, str, str]] = []

    async def recall(self, user_id: str, tenant_id: str, query: str) -> list[dict[str, Any]]:
        self.recall_calls.append((user_id, tenant_id, query))
        return self._memories


class FakeEventBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event_name: str, payload: dict[str, Any]) -> None:
        self.published.append((event_name, payload))


def _adapter(
    frames: list[str],
    memories: list[dict[str, Any]] | None = None,
) -> tuple[DifyConversationAdapter, FakeDifyClient, FakeMemory, FakeEventBus]:
    dify = FakeDifyClient([f.encode("utf-8") for f in frames])
    memory = FakeMemory(memories or [])
    bus = FakeEventBus()
    return DifyConversationAdapter(dify, memory, bus), dify, memory, bus


def _frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@pytest.mark.asyncio
async def test_stream_messages_injects_memory_and_encodes_dify_user() -> None:
    adapter, dify, memory, _bus = _adapter(
        frames=[],
        memories=[
            {"type": "preference", "content": "concise answers"},
            {"type": "fact", "content": "platform team"},
            {"type": "preference", "content": "zh-CN"},
        ],
    )

    async for _ in adapter.stream_messages("conv-1", "hello", "user-9", "tenant-3"):
        pass

    assert memory.recall_calls == [("user-9", "tenant-3", "hello")]
    path, body = dify.stream_calls[0]
    assert path == "/v1/chat-messages"
    assert body["user"] == "tenant-3:user-9"
    assert body["conversation_id"] == "conv-1"
    assert body["response_mode"] == "streaming"
    assert body["auto_generate_name"] is False
    assert body["inputs"]["tenant_id"] == "tenant-3"
    assert body["inputs"]["user_id"] == "user-9"
    assert body["inputs"]["user_preferences"] == "concise answers; zh-CN"
    assert body["inputs"]["user_facts"] == "platform team"


@pytest.mark.asyncio
async def test_stream_messages_new_conversation_and_files() -> None:
    adapter, _dify, _memory, _bus = _adapter(
        frames=[],
        memories=[],
    )

    files = [{"id": "upload-1", "type": "image"}, {"id": "upload-2"}]
    async for _ in adapter.stream_messages("", "hello", "user-9", "tenant-3", files=files):
        pass

    _path, body = _dify.stream_calls[0]
    assert body["conversation_id"] == ""
    assert body["auto_generate_name"] is True
    assert body["files"] == [
        {
            "type": "image",
            "transfer_method": "local_file",
            "upload_file_id": "upload-1",
        },
        {
            "type": "document",
            "transfer_method": "local_file",
            "upload_file_id": "upload-2",
        },
    ]


@pytest.mark.asyncio
async def test_maps_all_18_dify_events() -> None:
    frames = [
        _frame(
            "message",
            {"answer": "你", "message_id": "m1", "conversation_id": "c1", "task_id": "t1"},
        ),
        _frame(
            "agent_thought",
            {"id": "th1", "thought": "thinking", "tool": "search", "observation": "found"},
        ),
        _frame(
            "message_file",
            {"id": "f1", "type": "image", "url": "http://x/f.png", "belongs_to": "user"},
        ),
        _frame(
            "message_replace",
            {"answer": "replaced full text", "message_id": "m2", "task_id": "t2"},
        ),
        _frame("agent_message", {"message_id": "m2", "task_id": "t2", "answer": "agent reasoning"}),
        _frame("workflow_started", {"workflow_run_id": "wf1"}),
        _frame("node_started", {"data": {"node_id": "n1", "title": "retrieve", "index": 0}}),
        _frame("node_finished", {"data": {"node_id": "n1", "status": "success"}}),
        _frame(
            "parallel_branch_started", {"data": {"parallel_id": "p1", "parallel_branch_id": "b1"}}
        ),
        _frame("parallel_branch_finished", {"data": {"status": "success"}}),
        _frame("text_chunk", {"data": {"text": "chunk!"}}),
        _frame("text_replace", {"data": {"text": "replaced!"}}),
        _frame("tts_message", {"message_id": "m3", "task_id": "t3", "audio": "base64=="}),
        _frame("tts_message_end", {"audio": "base64=="}),
        _frame(
            "workflow_finished", {"data": {"status": "succeeded", "outputs": {}, "error": None}}
        ),
        _frame("error", {"code": "invoke_error", "message": "boom", "status": 500}),
        _frame(
            "message_end",
            {
                "id": "trace1",
                "conversation_id": "c1",
                "metadata": {
                    "usage": {"total_tokens": 10},
                    "retriever_resources": [
                        {
                            "document_name": "doc1",
                            "dataset_name": "kb1",
                            "content": "excerpt...",
                            "score": 0.9,
                        }
                    ],
                },
            },
        ),
        _frame("ping", {}),
    ]

    adapter, _dify, _memory, bus = _adapter(frames)
    events = [e async for e in adapter.stream_messages("c1", "hello", "u1", "tenant1")]

    names = [e["event"] for e in events]
    assert names == [
        "message",
        "agent_thought",
        "file",
        "replace",
        "agent_message",
        "workflow_started",
        "node_started",
        "node_finished",
        "branch_started",
        "branch_finished",
        "text_chunk",
        "text_replace",
        "tts_message",
        "tts_message_end",
        "workflow_finished",
        "error",
        "message_end",
    ]
    assert "ping" not in names  # keepalive is dropped

    by_name = {e["event"]: e["data"] for e in events}
    assert by_name["message"]["content"] == "你"
    assert by_name["message"]["messageId"] == "m1"
    assert by_name["agent_thought"]["thoughtId"] == "th1"
    assert by_name["file"]["url"] == "http://x/f.png"
    assert by_name["replace"]["answer"] == "replaced full text"
    assert by_name["replace"]["messageId"] == "m2"
    assert by_name["replace"]["taskId"] == "t2"
    assert by_name["agent_message"]["answer"] == "agent reasoning"
    assert by_name["workflow_started"]["workflowRunId"] == "wf1"
    assert by_name["node_started"]["nodeId"] == "n1"
    assert by_name["node_finished"]["status"] == "success"
    assert by_name["branch_started"]["parallelBranchId"] == "b1"
    assert by_name["branch_finished"]["status"] == "success"
    assert by_name["text_chunk"]["text"] == "chunk!"
    assert by_name["text_replace"]["text"] == "replaced!"
    assert by_name["tts_message"]["audio"] == "base64=="
    assert by_name["tts_message_end"]["audio"] == "base64=="
    assert by_name["workflow_finished"]["status"] == "succeeded"
    assert by_name["error"]["message"] == "boom"
    assert by_name["message_end"]["traceId"] == "trace1"
    message_end_metadata = by_name["message_end"]["metadata"]
    assert message_end_metadata["usage"] == {"total_tokens": 10}
    assert message_end_metadata["retrieverResources"][0]["sourceName"] == "doc1"
    assert message_end_metadata["retrieverResources"][0]["kbName"] == "kb1"
    assert message_end_metadata["retrieverResources"][0]["score"] == 0.9

    assert bus.published == [
        (
            "conversation.completed",
            {"conversationId": "c1", "userId": "u1", "tenantId": "tenant1"},
        )
    ]


@pytest.mark.asyncio
async def test_skips_malformed_json_chunk_without_breaking_stream() -> None:
    frames = [
        'event: message\ndata: {"answer": "ok"}\n\n',
        'event: message\ndata: {"answer": "broken",\n\n',
        'event: message\ndata: {"answer": "resumed"}\n\n',
    ]

    adapter, _dify, _memory, _bus = _adapter(frames)
    events = [e async for e in adapter.stream_messages("c1", "hello", "u1", "tenant1")]

    assert [e["data"]["content"] for e in events] == ["ok", "resumed"]


@pytest.mark.asyncio
async def test_reassembles_chunks_split_mid_line() -> None:
    full = 'event: message\ndata: {"answer": "hello world"}\n\n'
    split_at = len(full) // 3
    frames = [full[:split_at], full[split_at : 2 * split_at], full[2 * split_at :]]

    adapter, _dify, _memory, _bus = _adapter(frames)
    events = [e async for e in adapter.stream_messages("c1", "hello", "u1", "tenant1")]

    assert [e["data"]["content"] for e in events] == ["hello world"]


@pytest.mark.asyncio
async def test_handles_data_event_field_without_event_line() -> None:
    # Dify's Service API carries the event name inside the JSON payload.
    frames = [
        'data: {"event": "message", "answer": "inline"}\n\n',
        'data: {"event": "ping"}\n\n',
        'data: {"event": "error", "message": "failed", "code": "x", "status": 400}\n\n',
    ]

    adapter, _dify, _memory, _bus = _adapter(frames)
    events = [e async for e in adapter.stream_messages("c1", "hello", "u1", "tenant1")]

    names = [e["event"] for e in events]
    assert names == ["message", "error"]
    assert events[0]["data"]["content"] == "inline"
    assert events[1]["data"]["message"] == "failed"


@pytest.mark.asyncio
async def test_flushes_trailing_frame_without_newline() -> None:
    # The closing message_end frame has no trailing newline. Without a flush it
    # would be left in the buffer and conversation.completed would never fire.
    frames = [
        'event: message\ndata: {"answer": "hi"}\n\n',
        'event: message_end\ndata: {"id": "trace1", "conversation_id": "c1", "metadata": {}}',
    ]

    adapter, _dify, _memory, bus = _adapter(frames)
    events = [e async for e in adapter.stream_messages("c1", "hello", "u1", "tenant1")]

    assert [e["event"] for e in events] == ["message", "message_end"]
    assert bus.published == [
        (
            "conversation.completed",
            {"conversationId": "c1", "userId": "u1", "tenantId": "tenant1"},
        )
    ]


@pytest.mark.asyncio
async def test_event_type_resets_after_dispatch() -> None:
    # A data line without an explicit event: line (or JSON "event" key) must not
    # inherit the previous event's name.
    frames = [
        'event: message\ndata: {"answer": "first"}\n\n',
        'data: {"answer": "unnamed"}\n\n',
    ]

    adapter, _dify, _memory, _bus = _adapter(frames)
    events = [e async for e in adapter.stream_messages("c1", "hello", "u1", "tenant1")]

    assert [e["data"]["content"] for e in events] == ["first"]
