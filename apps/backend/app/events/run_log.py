"""Run-log write handler: persist a run log when a conversation turn completes.

The conversation adapter publishes ``conversation.completed`` on the process
bus with the raw ``message_end`` payload plus the stream's collected steps,
tool calls, output, and status (§13.1 P2: "conversation completed event ->
INSERT run_logs"). This handler extracts the trace and writes the row; it is
best-effort so a persistence failure never breaks the conversation itself.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.db import async_session_factory
from app.events.bus import bus
from app.services.run_log import RunLogService, TraceStepRecord, TraceToolCallRecord

log = structlog.get_logger(__name__)

RUN_LOG_WRITE_EVENT = "conversation.completed"


async def _handle_conversation_completed(
    sender: str,
    *,
    conversationId: str | None = None,
    userId: str | None = None,
    tenantId: str | None = None,
    agentId: str | None = None,
    agentName: str | None = None,
    userName: str | None = None,
    input: str | None = None,
    modelName: str | None = None,
    output: str | None = None,
    status: str | None = None,
    messageEnd: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
    toolCalls: list[dict[str, Any]] | None = None,
) -> None:
    """Extract and persist one run log from the completed turn's trace."""
    if not isinstance(messageEnd, dict):
        # No trace to record (a malformed upstream message_end) — nothing to write.
        return

    try:
        tenant_uuid = uuid.UUID(tenantId) if tenantId else None
        user_uuid = uuid.UUID(userId) if userId else None
    except (ValueError, TypeError, AttributeError):
        log.warning("run_log_skip_invalid_ids", conversation_id=conversationId)
        return
    if tenant_uuid is None or user_uuid is None or not agentId:
        return

    step_records = [_to_step_record(s) for s in steps or [] if isinstance(s, dict)]
    tool_records = [_to_tool_call_record(t) for t in toolCalls or [] if isinstance(t, dict)]

    try:
        async with async_session_factory() as session:
            await RunLogService(session).record_run_log(
                tenant_id=tenant_uuid,
                agent_id=agentId,
                user_id=user_uuid,
                agent_name=agentName,
                user_name=userName,
                status=status,
                input=input,
                output=output,
                model_name=modelName,
                message_end=messageEnd,
                steps=step_records,
                tool_calls=tool_records,
            )
    except Exception:
        # Best-effort: audit write failures must never fail the conversation.
        log.warning("run_log_persist_failed", exc_info=True)


def _to_step_record(data: dict[str, Any]) -> TraceStepRecord:
    return TraceStepRecord(
        step_order=data.get("step_order") or 0,
        name=data.get("name") or "",
        type=data.get("type"),
        status=data.get("status"),
        latency_ms=data.get("latency_ms"),
        detail=data.get("detail"),
    )


def _to_tool_call_record(data: dict[str, Any]) -> TraceToolCallRecord:
    return TraceToolCallRecord(
        tool_name=data.get("tool_name"),
        tool_id=data.get("tool_id"),
        status=data.get("status"),
        permission_mode=data.get("permission_mode"),
        latency_ms=data.get("latency_ms"),
        request_summary=data.get("request_summary"),
        response_summary=data.get("response_summary"),
    )


_registered = False


def register_run_log_handler() -> None:
    """Subscribe the run-log writer to the bus exactly once (idempotent)."""
    global _registered
    if _registered:
        return
    bus.subscribe(RUN_LOG_WRITE_EVENT, _handle_conversation_completed)
    _registered = True
