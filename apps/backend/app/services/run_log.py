"""RunLog application service: listing, detail, and trace extraction (§32.6).

The service owns the Dify ``message_end`` trace extraction and the run-log
write path, while ``RunLogRepository`` holds the tenant-scoped reads. Run-log
access is audit-only: ``RUN_LOG_READ_ROLES`` is the single source of truth
imported by the controller (route gate) and available for any future
defense-in-depth check.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models.run_log import RunLog, TraceCitation, TraceStep, TraceToolCall
from app.repositories.run_log import RunLogRepository
from app.schemas.run_log import (
    RunLogDetailDto,
    RunLogDto,
    RunLogPageDto,
    TraceCitationDto,
    TraceStepDto,
    TraceToolCallDto,
)

log = structlog.get_logger(__name__)

# Roles permitted to read run logs. ``employee`` is deliberately excluded: the
# audit trail is an admin/auditor concern (§32.6.2 gate).
RUN_LOG_READ_ROLES: tuple[str, ...] = (
    "platform_admin",
    "agent_admin",
    "knowledge_admin",
    "auditor",
)

# Citations are truncated to this many characters so a single knowledge hit
# cannot bloat a trace row.
_EXCERPT_MAX_CHARS = 200

# Dify workflow/tool statuses mapped onto the ``run_logs.status`` check
# constraint (success/failed/running/blocked). Unknown values fall through to
# ``None`` so a future upstream status can never violate the constraint.
_STATUS_MAP: dict[str, str] = {
    "success": "success",
    "succeeded": "success",
    "failed": "failed",
    "error": "failed",
    "running": "running",
    "blocked": "blocked",
    "stopped": "blocked",
}


@dataclass(frozen=True, slots=True)
class CitationData:
    """One extracted citation (Dify ``retriever_resources`` entry)."""

    source_name: str | None
    kb_name: str | None
    excerpt: str | None
    score: float | None


@dataclass(frozen=True, slots=True)
class MessageEndTrace:
    """Trace fields extracted from a Dify ``message_end`` payload."""

    trace_id: str | None
    conversation_id: str | None
    token_usage: int | None
    latency_ms: int | None
    citations: list[CitationData]


@dataclass(frozen=True, slots=True)
class TraceStepRecord:
    """One workflow step collected from the stream's node events."""

    step_order: int
    name: str
    type: str | None = None
    status: str | None = None
    latency_ms: int | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class TraceToolCallRecord:
    """One tool invocation collected from the stream's agent-thought events."""

    tool_name: str | None = None
    tool_id: str | None = None
    status: str | None = None
    permission_mode: str | None = None
    latency_ms: int | None = None
    request_summary: str | None = None
    response_summary: str | None = None


def extract_message_end_trace(data: dict[str, Any]) -> MessageEndTrace:
    """Extract trace data from a raw Dify ``message_end`` payload (§13.1 P1).

    ``metadata.usage`` becomes token/latency stats; ``metadata.retriever_resources``
    becomes citations. Every field is optional — a malformed or partial upstream
    payload yields ``None``/``[]`` rather than raising, so the caller never
    crashes mid-stream on a missing key or a non-object ``metadata``/``usage``.
    """
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    usage = metadata.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    latency = usage.get("latency")
    latency_ms = int(round(latency * 1000)) if isinstance(latency, (int, float)) else None
    resources = metadata.get("retriever_resources") or []
    if not isinstance(resources, list):
        resources = []
    citations = [
        CitationData(
            source_name=r.get("document_name"),
            kb_name=r.get("dataset_name"),
            excerpt=(r.get("content") or "")[:_EXCERPT_MAX_CHARS] or None,
            score=r.get("score"),
        )
        for r in resources
        if isinstance(r, dict)
    ]
    return MessageEndTrace(
        trace_id=data.get("id"),
        conversation_id=data.get("conversation_id"),
        token_usage=usage.get("total_tokens"),
        latency_ms=latency_ms,
        citations=citations,
    )


class RunLogService:
    """Reads run logs and records one run log from the stream's trace data."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = RunLogRepository()

    # ── reads ──

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        agent_id: str | None = None,
        conv_id: str | None = None,
        status: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> RunLogPageDto:
        rows, next_cursor = await self._repo.list_for_tenant(
            self._session,
            tenant_id=tenant_id,
            agent_id=agent_id,
            conversation_id=conv_id,
            status=status,
            from_date=_parse_date(from_date) if from_date else None,
            to_date=_parse_date(to_date) if to_date else None,
            limit=limit,
            cursor=cursor,
        )
        return RunLogPageDto(items=[_to_dto(row) for row in rows], nextCursor=next_cursor)

    async def get_by_trace_id(self, tenant_id: uuid.UUID, trace_id: str) -> RunLogDetailDto:
        """Return one run log's full trace (steps + citations + toolCalls)."""
        run_log = await self._repo.get_by_trace_id(self._session, tenant_id, trace_id)
        if run_log is None:
            raise AppError(404, "RUN_LOG_NOT_FOUND", "Run log not found")
        return _to_detail_dto(run_log)

    # ── write ──

    async def record_run_log(
        self,
        *,
        tenant_id: uuid.UUID,
        agent_id: str,
        user_id: uuid.UUID,
        agent_name: str | None,
        user_name: str | None,
        status: str | None,
        input: str | None,
        output: str | None,
        model_name: str | None,
        message_end: dict[str, Any],
        steps: Sequence[TraceStepRecord] | None = None,
        tool_calls: Sequence[TraceToolCallRecord] | None = None,
    ) -> RunLog:
        """Persist one run log from the stream's completed turn.

        Extracts the ``message_end`` trace (citations + usage), then writes the
        core row plus the steps/toolCalls collected from the node/tool events.
        ``tool_call_count`` is derived from the collected tool calls rather than
        hard-coded.
        """
        trace = extract_message_end_trace(message_end)
        run_log = RunLog(
            trace_id=trace.trace_id or str(uuid.uuid4()),
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            agent_name=agent_name,
            user_name=user_name,
            status=_normalize_status(status),
            input=input,
            output=output,
            model_name=model_name,
            token_usage=trace.token_usage,
            latency_ms=trace.latency_ms,
            tool_call_count=len(tool_calls) if tool_calls else 0,
            knowledge_hit_count=len(trace.citations),
            conversation_id=trace.conversation_id,
            # Dify's message id doubles as the platform trace id, so the Dify
            # reference column mirrors it.
            dify_message_id=trace.trace_id,
        )
        for citation in trace.citations:
            run_log.citations.append(
                TraceCitation(
                    source_name=citation.source_name,
                    kb_name=citation.kb_name,
                    excerpt=citation.excerpt,
                    score=citation.score,
                )
            )
        for step in steps or ():
            run_log.steps.append(
                TraceStep(
                    step_order=step.step_order,
                    name=step.name,
                    type=step.type,
                    status=step.status,
                    latency_ms=step.latency_ms,
                    detail=step.detail,
                )
            )
        for call in tool_calls or ():
            run_log.tool_calls.append(
                TraceToolCall(
                    tool_name=call.tool_name,
                    tool_id=call.tool_id,
                    status=call.status,
                    permission_mode=call.permission_mode,
                    latency_ms=call.latency_ms,
                    request_summary=call.request_summary,
                    response_summary=call.response_summary,
                )
            )
        self._session.add(run_log)
        await self._session.commit()
        return run_log


def _normalize_status(status: str | None) -> str | None:
    """Map an upstream run status onto the ``run_logs.status`` check constraint."""
    if status is None:
        return None
    return _STATUS_MAP.get(status)


def _parse_date(value: str) -> datetime:
    """Parse an ISO-8601 date/datetime filter, normalized to UTC.

    A naive input (``2026-09-19`` or a zone-less timestamp) is treated as UTC;
    an aware input is converted to UTC. Comparing a naive datetime against the
    ``timestamptz`` column would otherwise shift by the DB session timezone.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise AppError(400, "INVALID_DATE", f"Invalid date filter: {value}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _to_dto(run_log: RunLog) -> RunLogDto:
    return RunLogDto(
        traceId=run_log.trace_id,
        conversationId=run_log.conversation_id,
        agentId=run_log.agent_id,
        agentName=run_log.agent_name,
        userId=str(run_log.user_id),
        userName=run_log.user_name,
        status=run_log.status,
        input=run_log.input,
        modelName=run_log.model_name,
        tokenUsage=run_log.token_usage,
        latencyMs=run_log.latency_ms,
        toolCallCount=run_log.tool_call_count,
        knowledgeHitCount=run_log.knowledge_hit_count,
        createdAt=run_log.created_at,
    )


def _to_detail_dto(run_log: RunLog) -> RunLogDetailDto:
    base = _to_dto(run_log)
    return RunLogDetailDto(
        **base.model_dump(),
        output=run_log.output,
        steps=[
            TraceStepDto(
                stepOrder=step.step_order,
                name=step.name,
                type=step.type,
                status=step.status,
                latencyMs=step.latency_ms,
                detail=step.detail,
            )
            for step in sorted(run_log.steps, key=lambda s: s.step_order)
        ],
        citations=[
            TraceCitationDto(
                sourceName=c.source_name,
                kbName=c.kb_name,
                excerpt=c.excerpt,
                score=c.score,
            )
            for c in run_log.citations
        ],
        toolCalls=[
            TraceToolCallDto(
                toolName=t.tool_name,
                toolId=t.tool_id,
                status=t.status,
                permissionMode=t.permission_mode,
                latencyMs=t.latency_ms,
                requestSummary=t.request_summary,
                responseSummary=t.response_summary,
            )
            for t in run_log.tool_calls
        ],
    )
