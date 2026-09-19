"""Dashboard module response DTOs (architecture doc §15.1).

Both endpoints are read-only; field names are camelCase to match the platform's
public API contract (the agent/conversation DTOs use the same shape).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.agent import AgentDto
from app.schemas.conversation import ConversationDto


class MetricSummaryDto(BaseModel):
    """Admin four-dimension metrics over the current tenant, scoped to today.

    ``errorRate`` is the fraction of failed runs in ``[0, 1]`` (``0.0`` when
    there are no calls). ``avgLatencyMs`` is ``None`` when there are no calls.
    """

    totalAgents: int
    todayCalls: int
    avgLatencyMs: int | None = None
    errorRate: float


class TrendPointDto(BaseModel):
    """One hourly bucket of the rolling 24h call trend."""

    hour: datetime
    calls: int


class TopAgentDto(BaseModel):
    """One agent in the top-usage ranking (ordered by today's call count)."""

    agentId: str
    agentName: str | None = None
    calls: int


class AdminDashboardDto(BaseModel):
    """Response of ``GET /api/dashboard/admin``."""

    metrics: MetricSummaryDto
    trend: list[TrendPointDto]
    topAgents: list[TopAgentDto]


class UserHomeDto(BaseModel):
    """Response of ``GET /api/dashboard/user``."""

    recommendedAgents: list[AgentDto]
    recentConversations: list[ConversationDto]
    pendingTaskCount: int
