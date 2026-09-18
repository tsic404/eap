"""RunLogService + RunLogRepository tests: extraction, listing, detail, write."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.events.run_log as run_log_events
from app.errors import AppError
from app.models.agent import AgentRegistry
from app.models.run_log import RunLog, TraceCitation, TraceStep, TraceToolCall
from app.models.tenant import Tenant
from app.models.user import User
from app.services.run_log import (
    RunLogService,
    TraceStepRecord,
    TraceToolCallRecord,
    _normalize_status,
    _parse_date,
    extract_message_end_trace,
)

SAMPLE_MESSAGE_END: dict[str, object] = {
    "id": "msg-xxx",
    "conversation_id": "conv-xxx",
    "metadata": {
        "usage": {
            "total_tokens": 1161,
            "total_price": "0.0012890",
            "currency": "USD",
            "latency": 2.14,
        },
        "retriever_resources": [
            {
                "document_name": "采购合同审查要点 2026.pdf",
                "dataset_name": "合同与法务知识库",
                "segment_id": "seg-xxx",
                "score": 0.91,
                "content": "付款条款应明确验收标准",
            },
        ],
    },
}


async def _seed_tenant(session: AsyncSession) -> Tenant:
    tenant = Tenant(
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        sso_provider="local",
        status="active",
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def _seed_user(session: AsyncSession, tenant: Tenant, *, role: str = "auditor") -> User:
    user = User(
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@acme.com",
        name="Auditor",
        role=role,
        status="active",
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_agent(
    session: AsyncSession, tenant: Tenant, *, agent_id: str = "agent-1"
) -> AgentRegistry:
    agent = AgentRegistry(
        agent_id=agent_id,
        tenant_id=tenant.id,
        dify_app_id=f"dify-{agent_id}",
        name="Agent",
        type="chat",
        status="published",
        version=1,
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
    created_at: datetime | None = None,
    **overrides: object,
) -> RunLog:
    fields: dict[str, object] = {
        "trace_id": trace_id,
        "tenant_id": tenant.id,
        "agent_id": agent.agent_id,
        "user_id": user.id,
        "status": "success",
        "tool_call_count": 0,
        "knowledge_hit_count": 0,
    }
    fields.update(overrides)
    run_log = RunLog(**fields)  # type: ignore[arg-type]
    if created_at is not None:
        run_log.created_at = created_at
    session.add(run_log)
    await session.flush()
    return run_log


# ── extraction ──


def test_extract_message_end_trace_full() -> None:
    trace = extract_message_end_trace(SAMPLE_MESSAGE_END)

    assert trace.trace_id == "msg-xxx"
    assert trace.conversation_id == "conv-xxx"
    assert trace.token_usage == 1161
    assert trace.latency_ms == 2140
    assert len(trace.citations) == 1
    citation = trace.citations[0]
    assert citation.source_name == "采购合同审查要点 2026.pdf"
    assert citation.kb_name == "合同与法务知识库"
    assert citation.score == 0.91
    assert citation.excerpt == "付款条款应明确验收标准"


def test_extract_message_end_trace_missing_fields() -> None:
    trace = extract_message_end_trace({})

    assert trace.trace_id is None
    assert trace.conversation_id is None
    assert trace.token_usage is None
    assert trace.latency_ms is None
    assert trace.citations == []


def test_extract_message_end_trace_truncates_excerpt() -> None:
    long_content = "x" * 500
    data = {
        "id": "m1",
        "metadata": {
            "usage": {"total_tokens": 10},
            "retriever_resources": [{"document_name": "d", "content": long_content}],
        },
    }
    trace = extract_message_end_trace(data)

    assert len(trace.citations[0].excerpt or "") == 200


def test_extract_message_end_trace_non_dict_metadata() -> None:
    trace = extract_message_end_trace(
        {"id": "m1", "conversation_id": "c1", "metadata": "not-a-dict"}
    )

    assert trace.trace_id == "m1"
    assert trace.conversation_id == "c1"
    assert trace.token_usage is None
    assert trace.latency_ms is None
    assert trace.citations == []


def test_extract_message_end_trace_non_dict_usage_and_null_resources() -> None:
    trace = extract_message_end_trace(
        {"id": "m1", "metadata": {"usage": 123, "retriever_resources": None}}
    )

    assert trace.token_usage is None
    assert trace.latency_ms is None
    assert trace.citations == []


def test_extract_message_end_trace_non_list_resources() -> None:
    trace = extract_message_end_trace({"metadata": {"retriever_resources": "not-a-list"}})

    assert trace.citations == []


def test_normalize_status_maps_dify_values() -> None:
    assert _normalize_status("succeeded") == "success"
    assert _normalize_status("success") == "success"
    assert _normalize_status("failed") == "failed"
    assert _normalize_status("error") == "failed"
    assert _normalize_status("running") == "running"
    assert _normalize_status("stopped") == "blocked"
    assert _normalize_status("unknown-status") is None
    assert _normalize_status(None) is None


def test_parse_date_normalizes_to_utc() -> None:
    assert _parse_date("2026-09-19") == datetime(2026, 9, 19, tzinfo=UTC)
    assert _parse_date("2026-09-19T10:00:00") == datetime(2026, 9, 19, 10, 0, tzinfo=UTC)
    assert _parse_date("2026-09-19T10:00:00+08:00") == datetime(2026, 9, 19, 2, 0, tzinfo=UTC)


def test_parse_date_invalid_raises_400() -> None:
    with pytest.raises(AppError) as exc:
        _parse_date("not-a-date")
    assert exc.value.status_code == 400
    assert exc.value.code == "INVALID_DATE"


# ── list ──


@pytest.mark.asyncio
async def test_list_isolated_by_tenant(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant_a = await _seed_tenant(session)
        user_a = await _seed_user(session, tenant_a)
        agent_a = await _seed_agent(session, tenant_a)
        await _seed_run_log(session, tenant_a, user_a, agent_a, trace_id="trace-a")

        tenant_b = await _seed_tenant(session)
        user_b = await _seed_user(session, tenant_b)
        agent_b = await _seed_agent(session, tenant_b, agent_id="agent-2")
        await _seed_run_log(session, tenant_b, user_b, agent_b, trace_id="trace-b")

        page = await RunLogService(session).list(tenant_a.id)

        assert [item.traceId for item in page.items] == ["trace-a"]
        assert page.nextCursor is None


@pytest.mark.asyncio
async def test_list_filters_and_cursor_pagination(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant = await _seed_tenant(session)
        user = await _seed_user(session, tenant)
        agent = await _seed_agent(session, tenant)
        base = datetime.now(UTC) - timedelta(days=10)
        await _seed_run_log(
            session,
            tenant,
            user,
            agent,
            trace_id="t1",
            status="success",
            conversation_id="c1",
            created_at=base + timedelta(seconds=1),
        )
        await _seed_run_log(
            session,
            tenant,
            user,
            agent,
            trace_id="t2",
            status="failed",
            conversation_id="c2",
            created_at=base + timedelta(seconds=2),
        )
        await _seed_run_log(
            session,
            tenant,
            user,
            agent,
            trace_id="t3",
            status="success",
            conversation_id="c1",
            created_at=base + timedelta(seconds=3),
        )

        service = RunLogService(session)

        failed = await service.list(tenant.id, status="failed")
        assert [i.traceId for i in failed.items] == ["t2"]

        conv = await service.list(tenant.id, conv_id="c1")
        assert {i.traceId for i in conv.items} == {"t1", "t3"}

        page1 = await service.list(tenant.id, limit=2)
        assert [i.traceId for i in page1.items] == ["t3", "t2"]
        assert page1.nextCursor is not None

        page2 = await service.list(tenant.id, limit=2, cursor=page1.nextCursor)
        assert [i.traceId for i in page2.items] == ["t1"]
        assert page2.nextCursor is None


# ── detail ──


@pytest.mark.asyncio
async def test_get_by_trace_id_returns_three_dimensions(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant = await _seed_tenant(session)
        user = await _seed_user(session, tenant)
        agent = await _seed_agent(session, tenant)
        run_log = await _seed_run_log(session, tenant, user, agent, trace_id="trace-1")

        session.add(
            TraceStep(
                trace_id=run_log.trace_id,
                step_order=1,
                name="检索知识库",
                type="retrieval",
                status="success",
                latency_ms=120,
                detail="命中 3 条",
            )
        )
        session.add(
            TraceCitation(
                trace_id=run_log.trace_id,
                source_name="合同.pdf",
                kb_name="法务库",
                excerpt="付款条款",
                score=0.9,
            )
        )
        session.add(
            TraceToolCall(
                trace_id=run_log.trace_id,
                tool_name="http-tool",
                tool_id="t1",
                status="success",
                permission_mode="auto",
                latency_ms=40,
            )
        )
        await session.flush()

        detail = await RunLogService(session).get_by_trace_id(tenant.id, "trace-1")

        assert detail.traceId == "trace-1"
        assert [s.name for s in detail.steps] == ["检索知识库"]
        assert [c.sourceName for c in detail.citations] == ["合同.pdf"]
        assert [t.toolName for t in detail.toolCalls] == ["http-tool"]


@pytest.mark.asyncio
async def test_get_by_trace_id_cross_tenant_404(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant = await _seed_tenant(session)
        user = await _seed_user(session, tenant)
        agent = await _seed_agent(session, tenant)
        await _seed_run_log(session, tenant, user, agent, trace_id="trace-1")

        other = await _seed_tenant(session)

        with pytest.raises(AppError) as exc:
            await RunLogService(session).get_by_trace_id(other.id, "trace-1")
        assert exc.value.status_code == 404
        assert exc.value.code == "RUN_LOG_NOT_FOUND"


# ── write ──


@pytest.mark.asyncio
async def test_record_run_log_persists_extracted_trace(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant = await _seed_tenant(session)
        user = await _seed_user(session, tenant)
        agent = await _seed_agent(session, tenant)

        recorded = await RunLogService(session).record_run_log(
            tenant_id=tenant.id,
            agent_id=agent.agent_id,
            user_id=user.id,
            agent_name=agent.name,
            user_name=user.name,
            status="succeeded",
            input="帮我审查付款条款",
            output="已识别出付款风险",
            model_name="gpt-4",
            message_end=SAMPLE_MESSAGE_END,
            steps=[TraceStepRecord(step_order=0, name="检索知识库", status="success")],
            tool_calls=[TraceToolCallRecord(tool_name="http-tool", status="success")],
        )

        assert recorded.trace_id == "msg-xxx"
        assert recorded.conversation_id == "conv-xxx"
        assert recorded.token_usage == 1161
        assert recorded.latency_ms == 2140
        assert recorded.knowledge_hit_count == 1
        assert recorded.status == "success"  # "succeeded" mapped onto the check constraint
        assert recorded.tool_call_count == 1

        detail = await RunLogService(session).get_by_trace_id(tenant.id, "msg-xxx")
        assert detail.traceId == "msg-xxx"
        assert detail.conversationId == "conv-xxx"
        assert detail.knowledgeHitCount == 1
        assert [c.sourceName for c in detail.citations] == ["采购合同审查要点 2026.pdf"]
        assert [s.name for s in detail.steps] == ["检索知识库"]
        assert [t.toolName for t in detail.toolCalls] == ["http-tool"]
        assert detail.toolCallCount == 1


@pytest.mark.asyncio
async def test_record_run_log_generates_trace_id_when_missing(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant = await _seed_tenant(session)
        user = await _seed_user(session, tenant)
        agent = await _seed_agent(session, tenant)

        recorded = await RunLogService(session).record_run_log(
            tenant_id=tenant.id,
            agent_id=agent.agent_id,
            user_id=user.id,
            agent_name=agent.name,
            user_name=user.name,
            status=None,
            input=None,
            output=None,
            model_name=None,
            message_end={},
        )

        assert recorded.trace_id is not None
        assert recorded.knowledge_hit_count == 0
        assert recorded.token_usage is None
        assert recorded.status is None
        assert recorded.tool_call_count == 0

        row = await session.scalar(select(RunLog).where(RunLog.trace_id == recorded.trace_id))
        assert row is not None


# ── event handler ──


@pytest.mark.asyncio
async def test_conversation_completed_handler_persists_run_log(
    session_factory,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    async with session_factory() as session:
        tenant = await _seed_tenant(session)
        user = await _seed_user(session, tenant)
        agent = await _seed_agent(session, tenant)
        await session.commit()

    # Point the handler's session factory at the isolated test database.
    monkeypatch.setattr(run_log_events, "async_session_factory", session_factory)

    await run_log_events._handle_conversation_completed(
        "conversation.completed",
        conversationId="conv-1",
        userId=str(user.id),
        tenantId=str(tenant.id),
        agentId=agent.agent_id,
        agentName=agent.name,
        userName=user.name,
        input="hello",
        modelName=None,
        output="hi there",
        status="succeeded",
        messageEnd=SAMPLE_MESSAGE_END,
        steps=[{"step_order": 0, "name": "retrieve", "status": "success"}],
        toolCalls=[{"tool_name": "http-tool", "response_summary": "ok"}],
    )

    async with session_factory() as session:
        detail = await RunLogService(session).get_by_trace_id(tenant.id, "msg-xxx")
        assert detail.traceId == "msg-xxx"
        assert detail.status == "success"
        assert detail.output == "hi there"
        assert [s.name for s in detail.steps] == ["retrieve"]
        assert [t.toolName for t in detail.toolCalls] == ["http-tool"]


@pytest.mark.asyncio
async def test_conversation_completed_handler_skips_invalid_ids(
    session_factory,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    async with session_factory() as session:
        await _seed_tenant(session)
        await session.commit()

    monkeypatch.setattr(run_log_events, "async_session_factory", session_factory)

    # Malformed tenant/user ids must be a no-op, not a 500.
    await run_log_events._handle_conversation_completed(
        "conversation.completed",
        userId="user-1",
        tenantId="not-a-uuid",
        agentId="agent-1",
        messageEnd={},
    )

    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(RunLog))
        assert count == 0
