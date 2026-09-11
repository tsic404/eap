"""Translate Dify ``/v1/chat-messages`` SSE streams into platform SSE events.

The adapter depends on two narrow protocols — memory recall and event
publishing — rather than their concrete services, so it stays decoupled from
the memory (module 3) and event-bus (module 6) implementations and is testable
in isolation. See architecture doc §21.2.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import structlog

from app.services.dify_client import DifyClientService

logger = structlog.get_logger(__name__)


class MemoryRecall(Protocol):
    """Minimal memory-service contract the adapter needs."""

    async def recall(
        self,
        user_id: str,
        tenant_id: str,
        query: str,
    ) -> list[dict[str, Any]]: ...


class EventPublisher(Protocol):
    """Minimal event-bus contract the adapter needs."""

    async def publish(self, event_name: str, payload: dict[str, Any]) -> None: ...


class DifyConversationAdapter:
    """Convert a Dify chat-messages SSE stream into platform SSE event dicts."""

    def __init__(
        self,
        dify_client: DifyClientService,
        memory: MemoryRecall,
        event_bus: EventPublisher,
    ) -> None:
        self._dify = dify_client
        self._memory = memory
        self._event_bus = event_bus

    async def stream_messages(
        self,
        conv_id: str,
        query: str,
        user_id: str,
        tenant_id: str,
        files: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield platform SSE events for one Dify conversation turn.

        Each yielded item is ``{"event": <name>, "data": <payload>}`` in the
        platform's camelCase envelope.
        """
        dify_user = f"{tenant_id}:{user_id}"
        memories = await self._memory.recall(user_id, tenant_id, query) or []

        inputs: dict[str, Any] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "user_preferences": "; ".join(
                str(m.get("content", "")) for m in memories if m.get("type") == "preference"
            ),
            "user_facts": "; ".join(
                str(m.get("content", "")) for m in memories if m.get("type") == "fact"
            ),
        }
        body: dict[str, Any] = {
            "query": query,
            "user": dify_user,
            "response_mode": "streaming",
            "conversation_id": conv_id or "",
            "inputs": inputs,
            "auto_generate_name": not conv_id,
        }
        if files:
            body["files"] = [
                {
                    "type": f.get("type", "document"),
                    "transfer_method": "local_file",
                    "upload_file_id": f["id"],
                }
                for f in files
            ]

        async for event in self._stream_events(body, user_id, tenant_id):
            yield event

    async def _stream_events(
        self,
        body: dict[str, Any],
        user_id: str,
        tenant_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        buffer = ""
        event_type = ""
        async for chunk in self._dify.post_stream("/v1/chat-messages", body):
            buffer += chunk.decode("utf-8", errors="replace")
            lines = buffer.split("\n")
            # The trailing element is an incomplete line (or ""); keep it buffered.
            buffer = lines.pop() if lines else ""
            for line in lines:
                event_type, event = await self._handle_sse_line(
                    line, event_type, user_id, tenant_id
                )
                if event is not None:
                    yield event

        # Flush a final frame that ended without a trailing newline — e.g. the
        # closing ``message_end`` — so ``conversation.completed`` still fires.
        if buffer:
            _event_type, event = await self._handle_sse_line(buffer, event_type, user_id, tenant_id)
            if event is not None:
                yield event

    async def _handle_sse_line(
        self,
        line: str,
        event_type: str,
        user_id: str,
        tenant_id: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """Dispatch one SSE line; returns ``(next_event_type, event_or_none)``."""
        if line.startswith("event:"):
            return line[len("event:") :].strip(), None
        if not line.startswith("data:"):
            return event_type, None

        payload = line[len("data:") :].strip()
        if not payload:
            return "", None

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            # A truncated/garbled chunk must not kill the stream — skip it.
            # Log only the event name and byte length: the payload may contain
            # user conversation content and must never reach the log sink.
            logger.warning(
                "sse.parse_error",
                event_name=event_type or None,
                payload_bytes=len(payload.encode("utf-8")),
                error=str(exc),
            )
            return "", None

        # Dify's Service API carries the event name inside the JSON payload
        # ("event" key); the explicit ``event:`` line is a fallback for the
        # generic SSE framing. Reset after dispatch so a following ``data:``
        # line without its own event info never inherits a stale name.
        event_name = str(data.get("event") or event_type)
        event = _map_event(event_name, data)
        if event is None:
            return "", None

        if event_name == "message_end":
            await self._event_bus.publish(
                "conversation.completed",
                {
                    "conversationId": data.get("conversation_id"),
                    "userId": user_id,
                    "tenantId": tenant_id,
                },
            )

        return "", event


def _map_event(event_name: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Dify SSE event onto the platform envelope; ``None`` drops it."""
    match event_name:
        case "message":
            return {
                "event": "message",
                "data": {
                    "content": data.get("answer"),
                    "messageId": data.get("message_id"),
                    "conversationId": data.get("conversation_id"),
                    "taskId": data.get("task_id"),
                },
            }
        case "message_end":
            metadata = data.get("metadata") or {}
            return {
                "event": "message_end",
                "data": {
                    "traceId": data.get("id"),
                    "metadata": {
                        "usage": metadata.get("usage"),
                        "retrieverResources": [
                            {
                                "sourceName": r.get("document_name"),
                                "kbName": r.get("dataset_name"),
                                "excerpt": (r.get("content") or "")[:200],
                                "score": r.get("score"),
                            }
                            for r in metadata.get("retriever_resources", [])
                        ],
                    },
                },
            }
        case "message_file":
            return {
                "event": "file",
                "data": _file_payload(data),
            }
        case "message_replace":
            return {
                "event": "replace",
                "data": {
                    "answer": data.get("answer"),
                    "messageId": data.get("message_id"),
                    "taskId": data.get("task_id"),
                },
            }
        case "agent_message":
            return {
                "event": "agent_message",
                "data": {
                    "answer": data.get("answer"),
                    "messageId": data.get("message_id"),
                    "taskId": data.get("task_id"),
                },
            }
        case "agent_thought":
            return {
                "event": "agent_thought",
                "data": {
                    "thoughtId": data.get("id"),
                    "thought": data.get("thought"),
                    "tool": data.get("tool"),
                    "observation": data.get("observation"),
                },
            }
        case "workflow_started":
            return {
                "event": "workflow_started",
                "data": {"workflowRunId": data.get("workflow_run_id")},
            }
        case "workflow_finished":
            wf_data = data.get("data") or {}
            return {
                "event": "workflow_finished",
                "data": {
                    "status": wf_data.get("status"),
                    "outputs": wf_data.get("outputs"),
                    "error": wf_data.get("error"),
                },
            }
        case "node_started":
            node_data = data.get("data") or {}
            return {
                "event": "node_started",
                "data": {
                    "nodeId": node_data.get("node_id"),
                    "title": node_data.get("title"),
                    "index": node_data.get("index"),
                },
            }
        case "node_finished":
            node_data = data.get("data") or {}
            return {
                "event": "node_finished",
                "data": {
                    "nodeId": node_data.get("node_id"),
                    "status": node_data.get("status"),
                },
            }
        case "parallel_branch_started":
            branch_data = data.get("data") or {}
            return {
                "event": "branch_started",
                "data": {
                    "parallelId": branch_data.get("parallel_id"),
                    "parallelBranchId": branch_data.get("parallel_branch_id"),
                },
            }
        case "parallel_branch_finished":
            branch_data = data.get("data") or {}
            return {
                "event": "branch_finished",
                "data": {"status": branch_data.get("status")},
            }
        case "text_chunk":
            text_data = data.get("data") or {}
            return {"event": "text_chunk", "data": {"text": text_data.get("text")}}
        case "text_replace":
            text_data = data.get("data") or {}
            return {"event": "text_replace", "data": {"text": text_data.get("text")}}
        case "tts_message":
            return {
                "event": "tts_message",
                "data": {
                    "audio": data.get("audio"),
                    "messageId": data.get("message_id"),
                    "taskId": data.get("task_id"),
                },
            }
        case "tts_message_end":
            return {"event": "tts_message_end", "data": {"audio": data.get("audio")}}
        case "error":
            return {
                "event": "error",
                "data": {
                    "code": data.get("code"),
                    "message": data.get("message"),
                    "status": data.get("status"),
                },
            }
        case _:
            # Includes "ping" (keepalive) and any unknown/future event: dropped.
            return None


def _file_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": data.get("id"),
        "type": data.get("type"),
        "url": data.get("url"),
        "belongsTo": data.get("belongs_to"),
    }
