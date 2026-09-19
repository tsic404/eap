"""Dashboard application service: tenant-scoped read-only aggregation (§15.1).

Every query is constrained to ``tenant_id`` (and the user's own id where the
view is user-scoped), so a handler can never read another tenant's rows. The
service performs no writes — it only issues ``SELECT`` statements.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import BigInteger, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.models.agent import AgentRegistry
from app.models.conversation import Conversation
from app.models.run_log import RunLog
from app.models.task import Task
from app.schemas.agent import AgentDto, agent_to_dto
from app.schemas.conversation import ConversationDto
from app.schemas.dashboard import (
    AdminDashboardDto,
    MetricSummaryDto,
    TopAgentDto,
    TrendPointDto,
    UserHomeDto,
)

# Rolling 24h trend always returns a fixed number of hourly buckets so the
# frontend can render a stable axis even when the window is sparsely populated.
_TREND_HOURS = 24
_TOP_AGENTS_LIMIT = 10
_RECOMMENDED_AGENTS_LIMIT = 6
_RECENT_CONVERSATIONS_LIMIT = 5

_FAILED_STATUS = "failed"
_PENDING_STATUS = "pending"
_PUBLISHED_STATUS = "published"


def _hour_start(when: datetime) -> datetime:
    """Truncate ``when`` down to its UTC hour boundary."""
    return when.replace(minute=0, second=0, microsecond=0)


def _conversation_to_dto(conversation: Conversation) -> ConversationDto:
    return ConversationDto(
        id=str(conversation.id),
        title=conversation.title,
        agentId=conversation.agent_id,
        agentName=conversation.agent_name,
        status=conversation.status,
        createdAt=conversation.created_at,
        updatedAt=conversation.updated_at,
    )


class DashboardService:
    """Reads dashboard aggregates for the admin and user views."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_admin_dashboard(self, tenant_id: uuid.UUID) -> AdminDashboardDto:
        """Aggregate the admin view: four metrics, 24h trend, top agents."""
        now = datetime.now(UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        total_agents = await self._count_agents(tenant_id)
        today_calls, avg_latency, failed = await self._today_run_stats(tenant_id, today_start)
        error_rate = (failed / today_calls) if today_calls else 0.0

        return AdminDashboardDto(
            metrics=MetricSummaryDto(
                totalAgents=total_agents,
                todayCalls=today_calls,
                avgLatencyMs=round(avg_latency) if avg_latency is not None else None,
                errorRate=error_rate,
            ),
            trend=await self._hourly_trend(tenant_id, now),
            topAgents=await self._top_agents(tenant_id, today_start),
        )

    async def get_user_home(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> UserHomeDto:
        """Aggregate the user view: recommended agents, recent conversations, tasks."""
        return UserHomeDto(
            recommendedAgents=await self._recommended_agents(tenant_id),
            recentConversations=await self._recent_conversations(tenant_id, user_id),
            pendingTaskCount=await self._pending_task_count(tenant_id, user_id),
        )

    # -- admin aggregates ----------------------------------------------------

    async def _count_agents(self, tenant_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(AgentRegistry)
            .where(
                AgentRegistry.tenant_id == tenant_id,
                AgentRegistry.archived_at.is_(None),
            )
        )
        return int(await self._session.scalar(stmt) or 0)

    async def _today_run_stats(
        self, tenant_id: uuid.UUID, since: datetime
    ) -> tuple[int, float | None, int]:
        """Return ``(total, avg_latency_ms, failed)`` for runs since ``since``."""
        stmt = select(
            func.count().label("total"),
            func.count().filter(RunLog.status == _FAILED_STATUS).label("failed"),
            func.avg(RunLog.latency_ms).label("avg_latency"),
        ).where(RunLog.tenant_id == tenant_id, RunLog.created_at >= since)
        row = (await self._session.execute(stmt)).one()
        avg_latency = row.avg_latency
        return (
            int(row.total),
            float(avg_latency) if avg_latency is not None else None,
            int(row.failed),
        )

    async def _hourly_trend(self, tenant_id: uuid.UUID, now: datetime) -> list[TrendPointDto]:
        """Per-hour call counts for the 24 buckets ending at the current hour."""
        current_hour = _hour_start(now)
        window_start = current_hour - timedelta(hours=_TREND_HOURS - 1)
        # Bucket index is the UTC hour (epoch seconds // 3600), independent of
        # the DB session timezone, so the Python-side zero-fill keys always
        # align with the SQL grouping.
        bucket = func.floor(func.extract("epoch", RunLog.created_at) / 3600).cast(BigInteger)
        stmt = (
            select(bucket.label("bucket"), func.count().label("calls"))
            .where(RunLog.tenant_id == tenant_id, RunLog.created_at >= window_start)
            .group_by(bucket)
            .order_by(bucket)
        )
        rows = (await self._session.execute(stmt)).all()
        calls_by_bucket = {int(row.bucket): int(row.calls) for row in rows}

        points: list[TrendPointDto] = []
        for offset in range(_TREND_HOURS):
            hour = window_start + timedelta(hours=offset)
            points.append(
                TrendPointDto(
                    hour=hour,
                    calls=calls_by_bucket.get(int(hour.timestamp() // 3600), 0),
                )
            )
        return points

    async def _top_agents(self, tenant_id: uuid.UUID, since: datetime) -> list[TopAgentDto]:
        """Most-used agents since ``since``, by call count descending."""
        stmt = (
            select(
                RunLog.agent_id.label("agent_id"),
                AgentRegistry.name.label("agent_name"),
                func.count().label("calls"),
            )
            .join(
                AgentRegistry,
                (AgentRegistry.agent_id == RunLog.agent_id)
                & (AgentRegistry.tenant_id == RunLog.tenant_id),
            )
            .where(RunLog.tenant_id == tenant_id, RunLog.created_at >= since)
            .group_by(RunLog.agent_id, AgentRegistry.name)
            .order_by(func.count().desc(), RunLog.agent_id)
            .limit(_TOP_AGENTS_LIMIT)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            TopAgentDto(agentId=row.agent_id, agentName=row.agent_name, calls=int(row.calls))
            for row in rows
        ]

    # -- user aggregates -----------------------------------------------------

    async def _recommended_agents(self, tenant_id: uuid.UUID) -> list[AgentDto]:
        """Published agents ranked by 24h usage, then by publish recency."""
        since = datetime.now(UTC) - timedelta(hours=_TREND_HOURS)
        usage = (
            select(RunLog.agent_id.label("agent_id"), func.count().label("calls"))
            .where(RunLog.tenant_id == tenant_id, RunLog.created_at >= since)
            .group_by(RunLog.agent_id)
            .subquery()
        )
        stmt = (
            select(AgentRegistry)
            .outerjoin(usage, usage.c.agent_id == AgentRegistry.agent_id)
            .where(
                AgentRegistry.tenant_id == tenant_id,
                AgentRegistry.archived_at.is_(None),
                AgentRegistry.status == _PUBLISHED_STATUS,
            )
            .options(
                noload(AgentRegistry.knowledge_bindings),
                noload(AgentRegistry.tool_bindings),
                noload(AgentRegistry.daily_stats),
                noload(AgentRegistry.run_logs),
            )
            .order_by(
                func.coalesce(usage.c.calls, 0).desc(),
                AgentRegistry.published_at.desc().nullslast(),
                AgentRegistry.created_at.desc(),
            )
            .limit(_RECOMMENDED_AGENTS_LIMIT)
        )
        agents = list((await self._session.scalars(stmt)).all())
        return [agent_to_dto(agent) for agent in agents]

    async def _recent_conversations(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[ConversationDto]:
        stmt = (
            select(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
            .order_by(Conversation.created_at.desc())
            .limit(_RECENT_CONVERSATIONS_LIMIT)
        )
        conversations = list((await self._session.scalars(stmt)).all())
        return [_conversation_to_dto(conversation) for conversation in conversations]

    async def _pending_task_count(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Task)
            .where(
                Task.tenant_id == tenant_id,
                Task.assignee_id == user_id,
                Task.status == _PENDING_STATUS,
            )
        )
        return int(await self._session.scalar(stmt) or 0)
