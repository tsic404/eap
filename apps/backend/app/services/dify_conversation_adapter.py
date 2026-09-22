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

# Tool observations are truncated so a single run cannot bloat ``trace_tool_calls``.
_OBSERVATION_MAX_CHARS = 2000


def _new_trace_state() -> dict[str, Any]:
    """Fresh accumulator for one stream's run-log trace (§13.1 P1)."""
    return {
        "steps": {},  # node_id -> TraceStepRecord-shaped dict (insertion = node order)
        "tool_calls": [],
        "output": None,
        "status": None,
    }


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


class ToolApprovalGateway(Protocol):
    """Minimal tool-approval contract the adapter needs.

    ``request_approval`` returns ``None`` when the named tool is unknown or not
    gated, otherwise the approval descriptor the ``tool_approval_required``
    event carries.
    """

    async def request_approval(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None: ...


class DifyConversationAdapter:
    """Convert a Dify chat-messages SSE stream into platform SSE event dicts."""

    def __init__(
        self,
        dify_client: DifyClientService,
        memory: MemoryRecall,
        event_bus: EventPublisher,
        tool_approval: ToolApprovalGateway | None = None,
    ) -> None:
        self._dify = dify_client
        self._memory = memory
        self._event_bus = event_bus
        # ``None`` keeps the adapter usable in isolation (adapter unit tests);
        # production wiring (``ConversationService.stream``) always passes a
        # gateway so a gated tool surfaces ``tool_approval_required``.
        self._tool_approval = tool_approval
        self._truncation_notice = False

    async def stream_messages(
        self,
        conv_id: str,
        query: str,
        user_id: str,
        tenant_id: str,
        files: list[dict[str, Any]] | None = None,
        *,
        truncation_notice: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield platform SSE events for one Dify conversation turn.

        Each yielded item is ``{"event": <name>, "data": <payload>}`` in the
        platform's camelCase envelope. ``truncation_notice`` is surfaced on the
        closing ``message_end`` metadata so the client can show the token-limit
        banner without counting messages locally (§31.2.3).
        """
        self._truncation_notice = truncation_notice
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

        async for event in self._stream_events(body, user_id, tenant_id, _new_trace_state()):
            yield event

    async def _stream_events(
        self,
        body: dict[str, Any],
        user_id: str,
        tenant_id: str,
        trace: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        buffer = ""
        event_type = ""
        async for chunk in self._dify.post_stream("/v1/chat-messages", body):
            buffer += chunk.decode("utf-8", errors="replace")
            lines = buffer.split("\n")
            # The trailing element is an incomplete line (or ""); keep it buffered.
            buffer = lines.pop() if lines else ""
            for line in lines:
                event_type, events = await self._handle_sse_line(
                    line, event_type, user_id, tenant_id, trace
                )
                for event in events:
                    yield event

        # Flush a final frame that ended without a trailing newline — e.g. the
        # closing ``message_end`` — so ``conversation.completed`` still fires.
        if buffer:
            _event_type, events = await self._handle_sse_line(
                buffer, event_type, user_id, tenant_id, trace
            )
            for event in events:
                yield event

    async def _handle_sse_line(
        self,
        line: str,
        event_type: str,
        user_id: str,
        tenant_id: str,
        trace: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Dispatch one SSE line; returns ``(next_event_type, events)``.

        Usually zero or one event, but an ``agent_thought`` naming a gated tool
        also yields the ``tool_approval_required`` event it created.
        """
        if line.startswith("event:"):
            return line[len("event:") :].strip(), []
        if not line.startswith("data:"):
            return event_type, []

        payload = line[len("data:") :].strip()
        if not payload:
            return "", []

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
            return "", []

        if not isinstance(data, dict):
            # A non-object payload has no event fields; drop it rather than
            # crash on ``.get`` below.
            return "", []

        # Dify's Service API carries the event name inside the JSON payload
        # ("event" key); the explicit ``event:`` line is a fallback for the
        # generic SSE framing. Reset after dispatch so a following ``data:``
        # line without its own event info never inherits a stale name.
        event_name = str(data.get("event") or event_type)
        _accumulate_trace(event_name, data, trace)
        event = _map_event(event_name, data)
        if event is None:
            return "", []

        if event_name == "message_end":
            event["data"]["metadata"]["truncationNotice"] = self._truncation_notice
            await self._event_bus.publish(
                "conversation.completed",
                {
                    "conversationId": data.get("conversation_id"),
                    "userId": user_id,
                    "tenantId": tenant_id,
                    "messageEnd": data,
                    "steps": list(trace["steps"].values()),
                    "toolCalls": trace["tool_calls"],
                    "output": trace["output"],
                    "status": trace["status"],
                },
            )

        events = [event]
        if event_name == "agent_thought":
            approval = await self._request_tool_approval(data)
            if approval is not None:
                events.append(approval)
        return "", events

    async def _request_tool_approval(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """Bridge a Dify tool call to the platform approval gate.

        Returns the ``tool_approval_required`` event when the named tool is
        gated, else ``None``. Absent a wired gateway the adapter does not
        bridge — the platform only ever constructs it with one.

        Dify emits two ``agent_thought`` frames per tool call: a "before" frame
        carrying ``tool_input`` with an empty ``observation``, then an "after"
        echo carrying the ``observation`` but no ``tool_input``. Only the
        "before" frame may request approval — gating the echo would create a
        second pending task and later re-run the tool with empty params.
        """
        if self._tool_approval is None:
            return None
        tool_name = data.get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            return None
        if not data.get("tool_input") or data.get("observation"):
            return None
        approval = await self._tool_approval.request_approval(
            tool_name, _parse_tool_input(data.get("tool_input"))
        )
        if approval is None:
            return None
        return {"event": "tool_approval_required", "data": approval}


def _accumulate_trace(event_name: str, data: dict[str, Any], trace: dict[str, Any]) -> None:
    """Fold one Dify SSE event into the stream's run-log trace (§13.1 P1).

    Node start/finish pairs become steps (keyed by node id, so a finished node
    updates its started step in place); agent thoughts carrying a ``tool`` become
    tool calls; ``message``/``message_replace``/``agent_message`` update the final
    answer; ``workflow_finished`` records the run status.
    """
    if event_name == "node_started":
        node_data = data.get("data") or {}
        node_id = node_data.get("node_id")
        if node_id is not None:
            trace["steps"][node_id] = {
                "step_order": node_data.get("index") or 0,
                "name": node_data.get("title") or "",
                "type": "workflow",
                "status": "running",
                "latency_ms": None,
                "detail": None,
            }
    elif event_name == "node_finished":
        node_data = data.get("data") or {}
        node_id = node_data.get("node_id")
        step = trace["steps"].get(node_id)
        if step is not None:
            step["status"] = node_data.get("status")
    elif event_name == "agent_thought":
        tool = data.get("tool")
        if tool:
            observation = data.get("observation")
            trace["tool_calls"].append(
                {
                    "tool_name": tool,
                    "tool_id": None,
                    # Dify emits no tool_call_* event, and agent_thought carries
                    # no success/failure or latency signal. Hard-coding "success"
                    # would mis-render a failed run's tool point as green, so
                    # status and latency stay unknown (None).
                    "status": None,
                    "permission_mode": None,
                    "latency_ms": None,
                    "request_summary": None,
                    "response_summary": (
                        observation[:_OBSERVATION_MAX_CHARS] if observation else None
                    ),
                }
            )
    elif event_name in ("message", "message_replace"):
        answer = data.get("answer")
        if answer is not None:
            trace["output"] = answer
    elif event_name == "workflow_finished":
        workflow_data = data.get("data") or {}
        trace["status"] = workflow_data.get("status")


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


def _parse_tool_input(raw: Any) -> dict[str, Any]:
    """Decode Dify's ``agent_thought.tool_input`` JSON string into a params dict.

    Dify ships the tool arguments as a JSON-encoded string; anything absent,
    malformed, or non-object degrades to ``{}`` so a bad payload never breaks
    the approval request.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _file_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": data.get("id"),
        "type": data.get("type"),
        "url": data.get("url"),
        "belongsTo": data.get("belongs_to"),
    }
