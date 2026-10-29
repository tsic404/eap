"""Acceptance case for run-log gating, status normalization and metric scopes.

The case drives the public ``/api/run-logs``, ``/api/dashboard/*`` and
``/metrics`` surfaces end to end: PostgreSQL and Redis are real, the Dify
boundary is mocked, and every test seeds its own tenant so no clause depends on
another test's leftovers. C-21 defines success rate as ``1 - error rate``, so the
dashboard cases assert both halves of that pair together, including the empty
state where the error rate is 0 and the success rate is therefore 1.
"""

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from prometheus_client import REGISTRY

import app.events.run_log as run_log_events
import app.services.conversation as conversation_module
from app.core.exceptions import DifyApiError
from app.main import create_app
from app.models.agent import AgentRegistry
from app.models.conversation import Conversation
from app.models.run_log import RunLog, TraceCitation, TraceStep, TraceToolCall
from app.models.task import Task
from app.services.dify_client import DifyClientService

TC_ID = "TC-tech-ops-03"

_MESSAGES_PATH = "/api/conversations/{conversation_id}/messages"


def _chunk(event: str, data: dict[str, Any]) -> bytes:
    """One upstream SSE chunk, naming its event inside the JSON payload as Dify does."""
    return f"data: {json.dumps({'event': event, **data})}\n\n".encode()


class _Upstream:
    """Dify Service-API stand-in: a fixed raw SSE stream plus an empty history page."""

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = frames

    async def get(
        self, path: str, query: Any = None, *, retries: int = 3, timeout: Any = None
    ) -> dict[str, Any]:
        return {"data": [], "has_more": False}

    async def post_stream(self, path: str, body: Any):
        for frame in self._frames:
            yield frame

    async def aclose(self) -> None:
        pass


def _install_upstream(monkeypatch: pytest.MonkeyPatch, frames: list[bytes]) -> None:
    """Point the conversation service at a mocked Dify upstream emitting ``frames``."""
    upstream = _Upstream(frames)
    monkeypatch.setattr(conversation_module, "DifyClientService", lambda *a, **kw: upstream)


# ── module-local seeding helpers ────────────────────────────────────────────


async def _seed_agent(
    api,
    tenant,
    *,
    agent_id: str,
    status: str = "published",
    published_at: datetime | None = None,
    archived: bool = False,
) -> None:
    async with api.db.session() as session:
        session.add(
            AgentRegistry(
                agent_id=agent_id,
                tenant_id=tenant.id,
                dify_app_id=f"dify-{agent_id}",
                dify_api_key="app-secret",
                name=agent_id,
                type="chat",
                status=status,
                version=1,
                published_at=published_at,
                archived_at=datetime.now(UTC) if archived else None,
            )
        )
        await session.commit()


async def _seed_run_logs(api, tenant, user, specs: list[dict]) -> None:
    """Insert run logs in one transaction; ``specs`` supply the varying columns."""
    async with api.db.session() as session:
        for spec in specs:
            session.add(
                RunLog(
                    trace_id=spec["trace_id"],
                    tenant_id=tenant.id,
                    agent_id=spec["agent_id"],
                    user_id=user.id,
                    status=spec.get("status", "success"),
                    latency_ms=spec.get("latency_ms"),
                    conversation_id=spec.get("conversation_id"),
                    token_usage=spec.get("token_usage"),
                    tool_call_count=spec.get("tool_call_count", 0),
                    knowledge_hit_count=spec.get("knowledge_hit_count", 0),
                    created_at=spec.get("created_at") or datetime.now(UTC),
                )
            )
        await session.commit()


async def _seed_run_log_with_trace(api, tenant, user, **columns: object) -> None:
    """Insert one run log together with its three trace dimensions."""
    async with api.db.session() as session:
        log = RunLog(
            trace_id=columns["trace_id"],
            tenant_id=tenant.id,
            agent_id=columns["agent_id"],
            user_id=user.id,
            status=columns.get("status", "success"),
            latency_ms=columns.get("latency_ms"),
            token_usage=columns.get("token_usage"),
            conversation_id=columns.get("conversation_id"),
            tool_call_count=len(columns["tool_calls"]),
            knowledge_hit_count=len(columns["citations"]),
            created_at=datetime.now(UTC),
        )
        for step in columns["steps"]:
            log.steps.append(TraceStep(**step))
        for citation in columns["citations"]:
            log.citations.append(TraceCitation(**citation))
        for call in columns["tool_calls"]:
            log.tool_calls.append(TraceToolCall(**call))
        session.add(log)
        await session.commit()


async def _seed_conversation(
    api,
    tenant,
    user,
    *,
    agent_id: str,
    title: str,
    created_at: datetime,
    deleted: bool = False,
) -> None:
    async with api.db.session() as session:
        session.add(
            Conversation(
                tenant_id=tenant.id,
                user_id=user.id,
                agent_id=agent_id,
                agent_name=agent_id,
                title=title,
                created_at=created_at,
                deleted_at=datetime.now(UTC) if deleted else None,
            )
        )
        await session.commit()


async def _seed_task(api, tenant, *, creator, assignee, status: str = "pending") -> None:
    async with api.db.session() as session:
        session.add(
            Task(
                tenant_id=tenant.id,
                creator_id=creator.id,
                assignee_id=assignee.id if assignee is not None else None,
                type="tool_approval",
                title="Approve tool",
                priority="normal",
                status=status,
                payload={"tool_id": "tool-1"},
            )
        )
        await session.commit()


def _hour_floor(when: datetime) -> datetime:
    return when.replace(minute=0, second=0, microsecond=0)


def _parse_hour(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _items(api, headers: dict[str, str], query: str = "") -> list[dict]:
    resp = await api.client.get(f"/api/run-logs{query}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["items"]


# ── C-79: run-log gating ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_employee_is_denied_run_log_access(api) -> None:
    tenant, _admin = await api.seed(role="platform_admin")
    employee = await api.seed_user(tenant, role="employee")
    headers = api.auth(employee, tenant)

    listed = await api.client.get("/api/run-logs", headers=headers)
    detail = await api.client.get("/api/run-logs/trace-1", headers=headers)

    assert listed.status_code == 403
    assert listed.json()["error"]["code"] == "FORBIDDEN"
    assert detail.status_code == 403
    assert detail.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_admin_and_auditor_roles_can_read_run_logs(api) -> None:
    tenant, admin = await api.seed(role="platform_admin")
    await _seed_agent(api, tenant, agent_id="ops-bot")
    await _seed_run_logs(api, tenant, admin, [{"trace_id": "trace-1", "agent_id": "ops-bot"}])

    for role in ("platform_admin", "agent_admin", "knowledge_admin", "auditor"):
        user = admin if role == "platform_admin" else await api.seed_user(tenant, role=role)
        items = await _items(api, api.auth(user, tenant))
        assert [item["traceId"] for item in items] == ["trace-1"], role


@pytest.mark.asyncio
async def test_run_log_reads_are_filtered_to_the_authenticated_tenant(api) -> None:
    tenant_a, admin_a = await api.seed(role="auditor")
    tenant_b, admin_b = await api.seed(role="auditor")
    await _seed_agent(api, tenant_a, agent_id="scope-a")
    await _seed_agent(api, tenant_b, agent_id="scope-b")
    await _seed_run_logs(api, tenant_a, admin_a, [{"trace_id": "a-trace", "agent_id": "scope-a"}])
    await _seed_run_logs(api, tenant_b, admin_b, [{"trace_id": "b-trace", "agent_id": "scope-b"}])

    items_a = await _items(api, api.auth(admin_a, tenant_a))
    items_b = await _items(api, api.auth(admin_b, tenant_b))
    cross = await api.client.get("/api/run-logs/b-trace", headers=api.auth(admin_a, tenant_a))

    assert [item["traceId"] for item in items_a] == ["a-trace"]
    assert [item["traceId"] for item in items_b] == ["b-trace"]
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "RUN_LOG_NOT_FOUND"


@pytest.mark.asyncio
async def test_run_log_list_supports_agent_conversation_status_and_time_filters(api) -> None:
    tenant, auditor = await api.seed(role="auditor")
    await _seed_agent(api, tenant, agent_id="ops-a")
    await _seed_agent(api, tenant, agent_id="ops-b")
    now = datetime.now(UTC)
    await _seed_run_logs(
        api,
        tenant,
        auditor,
        [
            {
                "trace_id": "t1",
                "agent_id": "ops-a",
                "conversation_id": "conv-1",
                "status": "success",
                "created_at": now - timedelta(hours=1),
            },
            {
                "trace_id": "t2",
                "agent_id": "ops-b",
                "conversation_id": "conv-2",
                "status": "failed",
                "created_at": now - timedelta(hours=2),
            },
            {
                "trace_id": "t3",
                "agent_id": "ops-a",
                "conversation_id": "conv-2",
                "status": "blocked",
                "created_at": now - timedelta(hours=3),
            },
        ],
    )
    headers = api.auth(auditor, tenant)
    boundary = (now - timedelta(hours=2, minutes=30)).replace(tzinfo=None).isoformat()

    assert [item["traceId"] for item in await _items(api, headers, "?agentId=ops-a")] == [
        "t1",
        "t3",
    ]
    assert [item["traceId"] for item in await _items(api, headers, "?conversationId=conv-2")] == [
        "t2",
        "t3",
    ]
    assert [item["traceId"] for item in await _items(api, headers, "?status=failed")] == ["t2"]
    assert [item["traceId"] for item in await _items(api, headers, f"?from={boundary}")] == [
        "t1",
        "t2",
    ]
    assert [item["traceId"] for item in await _items(api, headers, f"?to={boundary}")] == ["t3"]


@pytest.mark.asyncio
async def test_run_log_list_pages_with_a_cursor(api) -> None:
    tenant, auditor = await api.seed(role="auditor")
    await _seed_agent(api, tenant, agent_id="page-bot")
    now = datetime.now(UTC)
    await _seed_run_logs(
        api,
        tenant,
        auditor,
        [
            {
                "trace_id": f"p{i}",
                "agent_id": "page-bot",
                "created_at": now - timedelta(hours=i),
            }
            for i in range(1, 4)
        ],
    )
    headers = api.auth(auditor, tenant)

    first = await api.client.get("/api/run-logs?limit=2", headers=headers)
    first_body = first.json()["data"]
    cursor = first_body["nextCursor"]
    assert [item["traceId"] for item in first_body["items"]] == ["p1", "p2"]
    assert cursor

    second = await api.client.get(f"/api/run-logs?limit=2&cursor={cursor}", headers=headers)
    second_body = second.json()["data"]
    assert [item["traceId"] for item in second_body["items"]] == ["p3"]
    assert second_body["nextCursor"] is None


# ── C-79: run-log detail ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_log_detail_exposes_steps_citations_tool_calls_and_stats(api) -> None:
    tenant, auditor = await api.seed(role="auditor")
    await _seed_agent(api, tenant, agent_id="detail-bot")
    await _seed_run_log_with_trace(
        api,
        tenant,
        auditor,
        trace_id="detail-trace",
        agent_id="detail-bot",
        conversation_id="conv-detail",
        status="success",
        latency_ms=2140,
        token_usage=1161,
        steps=[
            {
                "step_order": 0,
                "name": "retrieve",
                "type": "knowledge_retrieval",
                "status": "succeeded",
                "latency_ms": 120,
                "detail": "2 hits",
            },
            {"step_order": 1, "name": "llm", "type": "llm", "status": "running"},
        ],
        citations=[
            {
                "source_name": "contract.pdf",
                "kb_name": "Legal KB",
                "excerpt": "payment terms",
                "score": 0.91,
            }
        ],
        tool_calls=[
            {
                "tool_name": "search",
                "tool_id": "tool-1",
                "status": None,
                "permission_mode": "auto",
                "request_summary": "q=list",
                "response_summary": "2 rows",
            }
        ],
    )

    resp = await api.client.get("/api/run-logs/detail-trace", headers=api.auth(auditor, tenant))

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["tokenUsage"] == 1161
    assert body["latencyMs"] == 2140
    assert body["toolCallCount"] == 1
    assert body["knowledgeHitCount"] == 1
    assert [step["stepOrder"] for step in body["steps"]] == [0, 1]
    assert [step["name"] for step in body["steps"]] == ["retrieve", "llm"]
    assert body["steps"][0]["latencyMs"] == 120
    assert body["citations"][0] == {
        "sourceName": "contract.pdf",
        "kbName": "Legal KB",
        "excerpt": "payment terms",
        "score": 0.91,
    }
    assert body["toolCalls"][0]["toolName"] == "search"
    assert body["toolCalls"][0]["permissionMode"] == "auto"


@pytest.mark.asyncio
async def test_trace_step_and_tool_call_statuses_are_passed_through_verbatim(api) -> None:
    tenant, auditor = await api.seed(role="auditor")
    await _seed_agent(api, tenant, agent_id="passthrough-bot")
    await _seed_run_log_with_trace(
        api,
        tenant,
        auditor,
        trace_id="passthrough-trace",
        agent_id="passthrough-bot",
        steps=[
            {"step_order": 0, "name": "node", "status": "upstream_specific_state"},
        ],
        citations=[],
        tool_calls=[
            {"tool_name": "search", "status": "upstream_tool_state"},
            {"tool_name": "fetch", "status": None},
        ],
    )

    body = (
        await api.client.get("/api/run-logs/passthrough-trace", headers=api.auth(auditor, tenant))
    ).json()["data"]

    assert body["steps"][0]["status"] == "upstream_specific_state"
    # The detail DTO iterates the relationship without an order_by, so key the
    # assertion by tool name instead of relying on physical row order.
    assert {call["toolName"]: call["status"] for call in body["toolCalls"]} == {
        "search": "upstream_tool_state",
        "fetch": None,
    }


@pytest.mark.asyncio
async def test_agent_mode_tool_call_without_a_signal_is_written_with_a_null_status(
    api, monkeypatch
) -> None:
    # C-79: a tool call collected from an ``agent_thought`` frame carries no
    # success/failure signal, so the run log written from that stream must keep
    # ``status`` null. Seeding the row would bypass the writer under test, so the
    # turn runs through the real SSE -> run-log path.
    monkeypatch.setattr(run_log_events, "async_session_factory", api.db.session)

    tenant, auditor = await api.seed(role="auditor")
    await api.seed_agent(tenant, agent_id="signal-bot")
    headers = api.auth(auditor, tenant)

    created = await api.client.post(
        "/api/conversations", headers=headers, json={"agentId": "signal-bot"}
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["data"]["id"]

    _install_upstream(
        monkeypatch,
        [
            # Dify's own order: the tool call lands before the answer frames. The
            # ``message`` frame carrying the upstream conversation id is also the
            # one the service commits the request session on, so the run log
            # written at ``message_end`` outlives the request.
            _chunk(
                "agent_thought",
                {"tool": "search_kb", "tool_input": '{"query": "x"}', "observation": ""},
            ),
            _chunk(
                "message",
                {"answer": "hi", "message_id": "m0", "conversation_id": "dify-conv-1"},
            ),
            _chunk("workflow_finished", {"data": {"status": "succeeded"}}),
            _chunk("message_end", {"id": "signal-trace", "metadata": {}}),
        ],
    )
    streamed = await api.client.post(
        _MESSAGES_PATH.format(conversation_id=conversation_id),
        headers=headers,
        json={"query": "hi", "agentId": "signal-bot"},
    )
    assert streamed.status_code == 200, streamed.text

    detail = await api.client.get("/api/run-logs/signal-trace", headers=headers)

    assert detail.status_code == 200, detail.text
    body = detail.json()["data"]
    assert body["status"] == "success"
    assert body["toolCallCount"] == 1
    assert [call["toolName"] for call in body["toolCalls"]] == ["search_kb"]
    assert body["toolCalls"][0]["status"] is None


@pytest.mark.asyncio
async def test_upstream_run_states_are_folded_and_unmappable_states_become_null(
    api, monkeypatch
) -> None:
    # The writer (C-80) persists through the app-global session factory; point it
    # at the fixture's transaction so the write is readable through the public
    # API below. Harness wiring only — the write behaviour is untouched.
    monkeypatch.setattr(run_log_events, "async_session_factory", api.db.session)

    tenant, auditor = await api.seed(role="auditor")
    await api.seed_agent(tenant, agent_id="fold-bot")
    headers = api.auth(auditor, tenant)

    # Each turn's upstream reports a different terminal state; the run log it
    # writes must expose the folded value, and an unmappable state must be null.
    cases = (
        ("succeeded", "success"),
        ("error", "failed"),
        ("stopped", "blocked"),
        ("upstream-weird-state", None),
    )
    for index, (upstream, _folded) in enumerate(cases):
        created = await api.client.post(
            "/api/conversations", headers=headers, json={"agentId": "fold-bot"}
        )
        assert created.status_code == 201, created.text
        conversation_id = created.json()["data"]["id"]
        _install_upstream(
            monkeypatch,
            [
                _chunk(
                    "message",
                    {"answer": "hi", "message_id": f"m{index}", "conversation_id": "dify-conv-1"},
                ),
                _chunk("workflow_finished", {"data": {"status": upstream}}),
                _chunk("message_end", {"id": f"fold-trace-{index}", "metadata": {}}),
            ],
        )
        streamed = await api.client.post(
            _MESSAGES_PATH.format(conversation_id=conversation_id),
            headers=headers,
            json={"query": "hi", "agentId": "fold-bot"},
        )
        assert streamed.status_code == 200, streamed.text

    items = await _items(api, headers)
    statuses = {item["traceId"]: item["status"] for item in items}

    assert statuses == {f"fold-trace-{index}": folded for index, (_u, folded) in enumerate(cases)}


# ── C-21: admin dashboard ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_dashboard_counts_today_calls_and_excludes_blocked_from_failures(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await _seed_agent(api, tenant, agent_id="dash-bot")
    await _seed_agent(api, tenant, agent_id="gone-bot", archived=True)
    now = datetime.now(UTC)
    await _seed_run_logs(
        api,
        tenant,
        admin,
        [
            {
                "trace_id": "d1",
                "agent_id": "dash-bot",
                "status": "success",
                "latency_ms": 100,
                "created_at": now,
            },
            {
                "trace_id": "d2",
                "agent_id": "dash-bot",
                "status": "failed",
                "latency_ms": 200,
                "created_at": now,
            },
            {
                "trace_id": "d3",
                "agent_id": "dash-bot",
                "status": "blocked",
                "latency_ms": 300,
                "created_at": now,
            },
        ],
    )

    resp = await api.client.get("/api/dashboard/admin", headers=api.auth(admin, tenant))

    assert resp.status_code == 200
    metrics = resp.json()["data"]["metrics"]
    assert metrics["totalAgents"] == 1
    assert metrics["todayCalls"] == 3
    assert metrics["avgLatencyMs"] == 200
    assert metrics["errorRate"] == pytest.approx(1 / 3)
    assert metrics["successRate"] == pytest.approx(1 - 1 / 3)
    assert metrics["successRate"] == pytest.approx(1 - metrics["errorRate"])


@pytest.mark.asyncio
async def test_admin_dashboard_reports_empty_metric_defaults_without_today_run_logs(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await _seed_agent(api, tenant, agent_id="quiet-bot")

    body = (await api.client.get("/api/dashboard/admin", headers=api.auth(admin, tenant))).json()[
        "data"
    ]

    assert body["metrics"]["todayCalls"] == 0
    assert body["metrics"]["avgLatencyMs"] is None
    assert body["metrics"]["errorRate"] == 0.0
    assert body["metrics"]["successRate"] == 1.0
    assert body["metrics"]["totalAgents"] == 1
    assert body["topAgents"] == []
    assert sum(point["calls"] for point in body["trend"]) == 0


@pytest.mark.asyncio
async def test_admin_dashboard_trend_is_24_zero_filled_utc_hour_buckets(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await _seed_agent(api, tenant, agent_id="trend-bot")
    now = datetime.now(UTC)
    await _seed_run_logs(
        api,
        tenant,
        admin,
        [
            {"trace_id": "h1", "agent_id": "trend-bot", "created_at": now - timedelta(hours=2)},
            {"trace_id": "h2", "agent_id": "trend-bot", "created_at": now - timedelta(hours=3)},
            {"trace_id": "h3", "agent_id": "trend-bot", "created_at": now - timedelta(hours=30)},
        ],
    )

    trend = (await api.client.get("/api/dashboard/admin", headers=api.auth(admin, tenant))).json()[
        "data"
    ]["trend"]

    hours = [_parse_hour(point["hour"]) for point in trend]
    calls = {hour: point["calls"] for hour, point in zip(hours, trend, strict=True)}
    assert len(trend) == 24
    assert all(
        later - earlier == timedelta(hours=1)
        for earlier, later in zip(hours, hours[1:], strict=False)
    )
    assert calls[_hour_floor(now - timedelta(hours=2))] == 1
    assert calls[_hour_floor(now - timedelta(hours=3))] == 1
    assert sum(point["calls"] for point in trend) == 2


@pytest.mark.asyncio
async def test_admin_dashboard_top_agents_rank_by_today_calls_and_stop_at_ten(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    now = datetime.now(UTC)
    specs = []
    for index in range(11):
        agent_id = f"rank-{index:02d}"
        await _seed_agent(api, tenant, agent_id=agent_id)
        for call in range(index + 1):
            specs.append(
                {
                    "trace_id": f"{agent_id}-{call}",
                    "agent_id": agent_id,
                    "created_at": now,
                }
            )
    await _seed_run_logs(api, tenant, admin, specs)

    top = (await api.client.get("/api/dashboard/admin", headers=api.auth(admin, tenant))).json()[
        "data"
    ]["topAgents"]

    assert [entry["agentId"] for entry in top] == [
        f"rank-{index:02d}" for index in range(10, 0, -1)
    ]
    assert [entry["calls"] for entry in top] == list(range(11, 1, -1))


# ── C-21: user home ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_home_recommends_published_agents_by_usage_then_recency_capped_at_six(
    api,
) -> None:
    tenant, employee = await api.seed(role="employee")
    base = datetime.now(UTC)
    for offset, agent_id in enumerate(["zero-3", "zero-4", "zero-5", "zero-6"], start=3):
        await _seed_agent(
            api, tenant, agent_id=agent_id, published_at=base + timedelta(hours=offset)
        )
    await _seed_agent(api, tenant, agent_id="zero-old", published_at=base)
    await _seed_agent(api, tenant, agent_id="used-1", published_at=base)
    await _seed_agent(api, tenant, agent_id="used-2", published_at=base)
    await _seed_agent(api, tenant, agent_id="draft-bot", status="draft", published_at=base)
    await _seed_agent(
        api, tenant, agent_id="archived-bot", published_at=base + timedelta(hours=20), archived=True
    )
    now = datetime.now(UTC)
    await _seed_run_logs(
        api,
        tenant,
        employee,
        [
            {"trace_id": "u2-a", "agent_id": "used-2", "created_at": now},
            {"trace_id": "u2-b", "agent_id": "used-2", "created_at": now},
            {"trace_id": "u1-a", "agent_id": "used-1", "created_at": now},
        ],
    )

    home = (await api.client.get("/api/dashboard/user", headers=api.auth(employee, tenant))).json()[
        "data"
    ]

    ids = [agent["agentId"] for agent in home["recommendedAgents"]]
    assert ids == ["used-2", "used-1", "zero-6", "zero-5", "zero-4", "zero-3"]


@pytest.mark.asyncio
async def test_user_home_lists_own_undeleted_conversations_newest_first_capped_at_five(api) -> None:
    tenant, employee = await api.seed(role="employee")
    colleague = await api.seed_user(tenant, role="employee")
    await _seed_agent(api, tenant, agent_id="conv-bot")
    base = datetime.now(UTC)
    for index in range(1, 8):
        await _seed_conversation(
            api,
            tenant,
            employee,
            agent_id="conv-bot",
            title=f"c{index}",
            created_at=base - timedelta(minutes=100 - index),
        )
    await _seed_conversation(
        api,
        tenant,
        employee,
        agent_id="conv-bot",
        title="deleted",
        created_at=base,
        deleted=True,
    )
    await _seed_conversation(
        api, tenant, colleague, agent_id="conv-bot", title="other", created_at=base
    )

    home = (await api.client.get("/api/dashboard/user", headers=api.auth(employee, tenant))).json()[
        "data"
    ]

    assert [conv["title"] for conv in home["recentConversations"]] == [
        "c7",
        "c6",
        "c5",
        "c4",
        "c3",
    ]


@pytest.mark.asyncio
async def test_user_home_pending_task_count_counts_only_own_pending_assignments(api) -> None:
    tenant, employee = await api.seed(role="employee")
    colleague = await api.seed_user(tenant, role="employee")
    await _seed_task(api, tenant, creator=colleague, assignee=employee)
    await _seed_task(api, tenant, creator=colleague, assignee=employee)
    await _seed_task(api, tenant, creator=employee, assignee=colleague)
    await _seed_task(api, tenant, creator=colleague, assignee=employee, status="completed")
    await _seed_task(api, tenant, creator=colleague, assignee=None)

    home = (await api.client.get("/api/dashboard/user", headers=api.auth(employee, tenant))).json()[
        "data"
    ]

    assert home["pendingTaskCount"] == 2


# ── C-21 / C-19: metric scopes ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activity_without_a_run_log_contributes_to_no_metric(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    now = datetime.now(UTC)
    # Equal usage (zero run logs); the newer publication wins the tie-break, so
    # any metric that counted the conversation below would reorder this pair.
    await _seed_agent(api, tenant, agent_id="no-log-bot", published_at=now - timedelta(hours=2))
    await _seed_agent(api, tenant, agent_id="fresh-bot", published_at=now - timedelta(hours=1))
    await _seed_conversation(
        api,
        tenant,
        admin,
        agent_id="no-log-bot",
        title="unlogged turn",
        created_at=now,
    )
    await _seed_task(api, tenant, creator=admin, assignee=admin)

    body = (await api.client.get("/api/dashboard/admin", headers=api.auth(admin, tenant))).json()[
        "data"
    ]

    assert body["metrics"]["todayCalls"] == 0
    assert body["metrics"]["avgLatencyMs"] is None
    assert body["topAgents"] == []
    assert sum(point["calls"] for point in body["trend"]) == 0

    # C-21 scopes the user home to the same run-log accounting: the conversation
    # and the pending task above must not lift ``no-log-bot`` over ``fresh-bot``.
    home = (await api.client.get("/api/dashboard/user", headers=api.auth(admin, tenant))).json()[
        "data"
    ]

    assert [agent["agentId"] for agent in home["recommendedAgents"]] == [
        "fresh-bot",
        "no-log-bot",
    ]
    assert home["pendingTaskCount"] == 1


@contextlib.contextmanager
def _own_http_collectors() -> Iterator[None]:
    """Let the app created inside the block own the HTTP instrumentation collectors.

    prometheus-fastapi-instrumentator registers its collectors once per process
    and silently drops the instrumentation of every later app instance, so in a
    shared pytest process an app under test would either never be counted or be
    credited with an earlier case's traffic. Releasing the pre-existing
    collectors gives the block the single-instance behaviour of a deployment;
    they are restored on exit so the case neither depends on nor perturbs the
    order the suite runs in.
    """
    originals = [
        (collector, names)
        for collector, names in list(REGISTRY._collector_to_names.items())
        if any(name.startswith("http_") for name in names)
    ]
    for collector, _names in originals:
        REGISTRY.unregister(collector)
    try:
        yield
    finally:
        for collector, names in list(REGISTRY._collector_to_names.items()):
            if any(name.startswith("http_") for name in names):
                REGISTRY.unregister(collector)
        for collector, _names in originals:
            REGISTRY.register(collector)


@pytest.mark.asyncio
async def test_metrics_endpoint_is_public_and_excluded_from_its_own_http_counters(api) -> None:
    with _own_http_collectors():
        app = create_app(api.settings)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # A plain request is counted, proving the HTTP counters are live...
            unauthenticated = await client.get("/api/run-logs")
            assert unauthenticated.status_code == 401

            first = await client.get("/metrics")
            assert first.status_code == 200
            # The block owns the collectors, so this call's contribution is the
            # whole series: no authentication, one templated handler.
            counted = 'http_requests_total{handler="/api/run-logs",method="GET",status="401"} 1.0'
            assert counted in first.text

            # ...while this endpoint and the health probes are never counted.
            health = await client.get("/api/health/live")
            assert health.status_code == 200

            body = (await client.get("/metrics")).text
            assert 'handler="/metrics"' not in body
            assert 'handler="/api/health' not in body


def _outcome_totals(body: str) -> dict[str, float]:
    """Sum ``dify_api_requests_total`` by its ``outcome`` label across paths."""
    totals: dict[str, float] = {}
    for line in body.splitlines():
        if not line.startswith("dify_api_requests_total{"):
            continue
        sample, _, value = line.rpartition(" ")
        outcome = sample.split('outcome="', 1)[1].split('"', 1)[0]
        totals[outcome] = totals.get(outcome, 0.0) + float(value)
    return totals


@pytest.mark.asyncio
async def test_dify_call_counter_classifies_every_outcome_class(api) -> None:
    """C-19: inference-call volume is classified success/retry/failure/circuit-open."""
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)

    def _upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/healthy":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(502, text="upstream down")

    before = _outcome_totals((await api.client.get("/metrics", headers=headers)).text)

    dify = DifyClientService("http://dify.test", "secret", transport=httpx.MockTransport(_upstream))
    try:
        await dify.get("/v1/healthy", retries=0)
        # Five transient 502s open the per-path breaker; the sixth call is
        # rejected before it reaches the upstream.
        for _ in range(6):
            with pytest.raises(DifyApiError):
                await dify.get("/v1/down", retries=0)
    finally:
        await dify.aclose()

    after = _outcome_totals((await api.client.get("/metrics", headers=headers)).text)

    deltas = {
        outcome: after.get(outcome, 0.0) - before.get(outcome, 0.0)
        for outcome in ("success", "retry", "failure", "circuit_open")
    }
    assert deltas == {"success": 1.0, "retry": 5.0, "failure": 5.0, "circuit_open": 1.0}


@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_the_documented_metric_domains(api) -> None:
    body = (await api.client.get("/metrics")).text

    # HTTP request counters/latency for the rest of the API...
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    # ...plus inference-call volume/latency, DB pool usage and audit queue depth.
    assert "dify_api_requests_total" in body
    assert "dify_api_request_duration_seconds" in body
    assert "db_pool_size" in body
    assert "db_pool_checkedout" in body
    assert "db_pool_overflow" in body
    assert "rq_audit_log_queue_depth" in body
