"""DashboardService tests: tenant-scoped aggregation correctness (§15.1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentRegistry
from app.models.conversation import Conversation
from app.models.run_log import RunLog
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.user import User
from app.services.dashboard import DashboardService


async def _seed_tenant(session: AsyncSession, *, name: str = "Acme") -> Tenant:
    tenant = Tenant(
        name=name,
        slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}",
        sso_provider="local",
        status="active",
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def _seed_user(
    session: AsyncSession, tenant: Tenant, *, role: str = "employee", name: str = "User"
) -> User:
    user = User(
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@acme.com",
        name=name,
        role=role,
        status="active",
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_agent(
    session: AsyncSession,
    tenant: Tenant,
    *,
    agent_id: str,
    status: str = "published",
    archived: bool = False,
) -> AgentRegistry:
    agent = AgentRegistry(
        agent_id=agent_id,
        tenant_id=tenant.id,
        dify_app_id=f"dify-{agent_id}",
        name=f"Agent {agent_id}",
        type="chat",
        status=status,
        version=1,
        published_at=datetime.now(UTC) if status == "published" else None,
        archived_at=datetime.now(UTC) if archived else None,
    )
    session.add(agent)
    await session.flush()
    return agent


async def _seed_run_log(
    session: AsyncSession,
    tenant: Tenant,
    user: User,
    agent: AgentRegistry,
    *,
    trace_id: str,
    status: str = "success",
    latency_ms: int | None = None,
    created_at: datetime | None = None,
) -> RunLog:
    run_log = RunLog(
        trace_id=trace_id,
        tenant_id=tenant.id,
        agent_id=agent.agent_id,
        user_id=user.id,
        status=status,
        latency_ms=latency_ms,
        tool_call_count=0,
        knowledge_hit_count=0,
        created_at=created_at if created_at is not None else datetime.now(UTC),
    )
    session.add(run_log)
    await session.flush()
    return run_log


async def _seed_conversation(
    session: AsyncSession,
    tenant: Tenant,
    user: User,
    agent: AgentRegistry,
    *,
    title: str = "Hello",
) -> Conversation:
    conversation = Conversation(
        tenant_id=tenant.id,
        user_id=user.id,
        agent_id=agent.agent_id,
        agent_name=agent.name,
        title=title,
        status="active",
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def _seed_task(
    session: AsyncSession,
    tenant: Tenant,
    creator: User,
    assignee: User,
    *,
    status: str = "pending",
) -> Task:
    task = Task(
        tenant_id=tenant.id,
        creator_id=creator.id,
        assignee_id=assignee.id,
        type="tool_approval",
        title="Approve tool",
        priority="normal",
        status=status,
        payload={"tool_id": "t1"},
    )
    session.add(task)
    await session.flush()
    return task


# ── admin dashboard ──


@pytest.mark.asyncio
async def test_admin_dashboard_metrics_scoped_to_tenant_and_today(session_factory) -> None:
    async with session_factory() as session:
        tenant_a = await _seed_tenant(session, name="TenantA")
        tenant_b = await _seed_tenant(session, name="TenantB")
        user_a = await _seed_user(session, tenant_a)
        user_b = await _seed_user(session, tenant_b)

        agent_a1 = await _seed_agent(session, tenant_a, agent_id="a1")
        await _seed_agent(session, tenant_a, agent_id="a2")
        await _seed_agent(session, tenant_a, agent_id="a3", archived=True)
        agent_b1 = await _seed_agent(session, tenant_b, agent_id="b1")

        # Today's runs for tenant A: 100ms success + 300ms failed → avg 200, err 0.5.
        await _seed_run_log(session, tenant_a, user_a, agent_a1, trace_id="t1", latency_ms=100)
        await _seed_run_log(
            session, tenant_a, user_a, agent_a1, trace_id="t2", status="failed", latency_ms=300
        )
        # Old run (2 days ago): outside "today" and outside the 24h trend.
        await _seed_run_log(
            session,
            tenant_a,
            user_a,
            agent_a1,
            trace_id="t3",
            created_at=datetime.now(UTC) - timedelta(days=2),
        )
        # Cross-tenant run that must never appear.
        await _seed_run_log(session, tenant_b, user_b, agent_b1, trace_id="t4")

        dashboard = await DashboardService(session).get_admin_dashboard(tenant_a.id)

        assert dashboard.metrics.totalAgents == 2
        assert dashboard.metrics.todayCalls == 2
        assert dashboard.metrics.avgLatencyMs == 200
        assert dashboard.metrics.errorRate == 0.5
        assert {point.agentId for point in dashboard.topAgents} == {"a1"}
        assert sum(point.calls for point in dashboard.trend) == 2


@pytest.mark.asyncio
async def test_admin_dashboard_empty_metrics(session_factory) -> None:
    async with session_factory() as session:
        tenant = await _seed_tenant(session)

        dashboard = await DashboardService(session).get_admin_dashboard(tenant.id)

        assert dashboard.metrics.totalAgents == 0
        assert dashboard.metrics.todayCalls == 0
        assert dashboard.metrics.avgLatencyMs is None
        assert dashboard.metrics.errorRate == 0.0
        assert dashboard.topAgents == []
        assert len(dashboard.trend) == 24
        assert sum(point.calls for point in dashboard.trend) == 0


@pytest.mark.asyncio
async def test_top_agents_ranked_by_calls_descending(session_factory) -> None:
    async with session_factory() as session:
        tenant = await _seed_tenant(session)
        user = await _seed_user(session, tenant)
        busy = await _seed_agent(session, tenant, agent_id="busy")
        idle = await _seed_agent(session, tenant, agent_id="idle")

        for index in range(3):
            await _seed_run_log(session, tenant, user, busy, trace_id=f"busy-{index}")
        await _seed_run_log(session, tenant, user, idle, trace_id="idle-1")

        dashboard = await DashboardService(session).get_admin_dashboard(tenant.id)

        assert [point.agentId for point in dashboard.topAgents] == ["busy", "idle"]
        assert dashboard.topAgents[0].calls == 3
        assert dashboard.topAgents[1].calls == 1


# ── user home ──


@pytest.mark.asyncio
async def test_user_home_recommended_agents_exclude_draft_and_archived(session_factory) -> None:
    async with session_factory() as session:
        tenant = await _seed_tenant(session)
        user = await _seed_user(session, tenant)
        await _seed_agent(session, tenant, agent_id="published")
        await _seed_agent(session, tenant, agent_id="draft", status="draft")
        await _seed_agent(session, tenant, agent_id="gone", archived=True)

        home = await DashboardService(session).get_user_home(tenant.id, user.id)

        assert [agent.agentId for agent in home.recommendedAgents] == ["published"]


@pytest.mark.asyncio
async def test_user_home_recent_conversations_scoped_to_user(session_factory) -> None:
    async with session_factory() as session:
        tenant = await _seed_tenant(session)
        me = await _seed_user(session, tenant, name="Me")
        other = await _seed_user(session, tenant, name="Other")
        agent = await _seed_agent(session, tenant, agent_id="agent-1")

        mine = await _seed_conversation(session, tenant, me, agent, title="mine")
        await _seed_conversation(session, tenant, other, agent, title="theirs")

        home = await DashboardService(session).get_user_home(tenant.id, me.id)

        assert [conv.id for conv in home.recentConversations] == [str(mine.id)]


@pytest.mark.asyncio
async def test_user_home_pending_task_count_scoped_to_assignee(session_factory) -> None:
    async with session_factory() as session:
        tenant = await _seed_tenant(session)
        creator = await _seed_user(session, tenant, name="Creator")
        me = await _seed_user(session, tenant, name="Me")
        other = await _seed_user(session, tenant, name="Other")

        await _seed_task(session, tenant, creator, me, status="pending")
        await _seed_task(session, tenant, creator, me, status="completed")
        await _seed_task(session, tenant, creator, other, status="pending")

        home = await DashboardService(session).get_user_home(tenant.id, me.id)

        assert home.pendingTaskCount == 1
