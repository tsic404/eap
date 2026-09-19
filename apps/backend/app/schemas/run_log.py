"""RunLog module response DTOs (architecture doc §32.6.2).

Field names are camelCase to match the platform's public API contract (the
conversation DTOs and the frontend ``RunLog`` type use the same shape). The run
log is an audit read-only resource, so there are no request-body DTOs — only the
two GET endpoints' response shapes.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TraceStepDto(BaseModel):
    """One workflow step on a trace's timeline."""

    stepOrder: int
    name: str
    type: str | None = None
    status: str | None = None
    latencyMs: int | None = None
    detail: str | None = None


class TraceCitationDto(BaseModel):
    """A knowledge-base hit cited by the run."""

    sourceName: str | None = None
    kbName: str | None = None
    excerpt: str | None = None
    score: float | None = None


class TraceToolCallDto(BaseModel):
    """A tool invocation made during the run.

    ``status`` and ``latencyMs`` stay ``None`` for Agent-mode tool calls: Dify's
    only tool-call signal there is ``agent_thought``, which carries neither a
    success/failure flag nor a per-call latency. They are intentionally not
    hard-coded to "success" — that would mis-render a failed call as green.
    """

    toolName: str | None = None
    toolId: str | None = None
    status: str | None = None
    permissionMode: str | None = None
    latencyMs: int | None = None
    requestSummary: str | None = None
    responseSummary: str | None = None


class RunLogDto(BaseModel):
    """Public list view: one run log's core fields (no child rows)."""

    traceId: str
    conversationId: str | None = None
    agentId: str
    agentName: str | None = None
    userId: str
    userName: str | None = None
    status: str | None = None
    input: str | None = None
    modelName: str | None = None
    tokenUsage: int | None = None
    latencyMs: int | None = None
    toolCallCount: int
    knowledgeHitCount: int
    createdAt: datetime


class RunLogDetailDto(RunLogDto):
    """Detail view: the assistant answer plus the three trace dimensions (§32.6)."""

    output: str | None = None
    steps: list[TraceStepDto]
    citations: list[TraceCitationDto]
    toolCalls: list[TraceToolCallDto]


class RunLogPageDto(BaseModel):
    """Cursor-paginated run-log listing."""

    items: list[RunLogDto]
    nextCursor: str | None = None
