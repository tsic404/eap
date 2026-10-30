"""Seed the data the tool/ops browser journeys assert on.

Invoked by the host harness (``apps/frontend/e2e/global-setup.mjs``) and by the
container runner (``scripts/e2e.sh``), always inside the backend environment so
the application's own models, session factory, password hash and schema apply:

    python - < deploy/e2e/seed_fixtures.py

stdout carries the manifest the journeys read (task ids and titles, tool ids,
the stub upstream's URLs, the dashboard's own account); everything else goes to
stderr, so the caller can redirect stdout straight into a JSON file.

Scope: ``E2E_FIXTURE_SPEC`` names the journey about to run. An unset value means
the whole suite is about to run and every fixture is seeded; a known journey
seeds only that journey's rows; any other spec seeds nothing at all, so a
long-lived QA stack keeps its accumulated state (``make e2e`` for an unrelated
spec must not touch the shared tenant).

Every fixture is addressed by a fixed key and deleted before it is re-created,
and every delete is scoped to the tenant that owns the fixture: tool ids are a
global primary key, so an unscoped delete could remove another tenant's tool.
The dashboard fixtures live in a tenant of their own, which is what lets their
run logs be replaced without clearing the shared tenant's evidence.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text

from app.auth.password import hash_password
from app.core.folding import fold
from app.db import async_session_factory
from app.models.agent import AgentRegistry
from app.models.conversation import Conversation
from app.models.run_log import RunLog, TraceCitation, TraceStep, TraceToolCall
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.tool import ToolRegistry
from app.models.user import ACCOUNT_SOURCE_LOCAL, LOCAL_SUBJECT_PREFIX, User

TENANT_DOMAIN = "acme.com"
# Rows mirror ``deploy/e2e/seed_accounts.py``; the shared password keeps one
# credential for every seeded local account.
PASSWORD = "Local-Account-1"
ADMIN_EMAIL = "carol@acme.com"
EMPLOYEE_EMAIL = "dave@acme.com"

# The dashboard asserts tenant-wide totals, so its rows live in a tenant no
# other journey writes to; replacing its run logs then clears nothing shared.
METRICS_DOMAIN = "metrics.acme.com"
METRICS_SLUG = "e2e-metrics"
METRICS_TENANT_NAME = "指标夹具租户"
METRICS_ADMIN_EMAIL = "carol@metrics.acme.com"
METRICS_ADMIN_NAME = "Carol"

# Fixed ids keep the manifest stable across runs and let the seed delete the
# previous run's rows. The UUIDs are literals because the journeys address the
# same tasks through the manifest.
TASKS = {
    "approve": ("11111111-1111-4111-8111-111111111111", "工具审批：执行链探针"),
    "timeout": ("22222222-2222-4222-8222-222222222222", "工具审批：超时探针"),
    "reject": ("33333333-3333-4333-8333-333333333333", "工具审批：拒绝探针"),
    "rejected": ("44444444-4444-4444-8444-444444444444", "工具审批：已驳回探针"),
    "cancelled": ("55555555-5555-4555-8555-555555555555", "工具审批：已取消探针"),
}

# The status each task row is inserted with; the manifest repeats it so a
# journey can state which state its assertion started from.
TASK_SEEDED_STATUS = {
    "approve": "pending",
    "timeout": "pending",
    "reject": "pending",
    "rejected": "rejected",
    "cancelled": "cancelled",
}

TOOLS = {
    "approve": "e2e-tool-approve",
    "denied": "e2e-tool-denied",
}

# The isolation journey needs a positive row on each side, while the shared acme
# tenant must stay empty (the first-login journey asserts that). Two dedicated
# tenants keep both sides isolated; the manifest below is what the spec reads, so
# a renamed fixture reaches the assertion instead of silently passing.
TENANT_A_DOMAIN = "initech.com"
TENANT_A_SLUG = "e2e-initech"
TENANT_A_NAME = "租户 A 夹具"
TENANT_A_USER_EMAIL = "eve@initech.com"
TENANT_A_USER_NAME = "Eve"
TENANT_A_AGENT_ID = "initech-agent-1"
TENANT_A_AGENT_NAME = "租户 A 智能体"
TENANT_A_AGENT_DESCRIPTION = "仅租户 A 可见的智能体"
TENANT_A_DIFY_APP_ID = "initech-app-1"
TENANT_A_CONVERSATION_ID = uuid.UUID("22222222-3333-4444-8555-666666666666")
TENANT_A_CONVERSATION_TITLE = "租户 A 会话"

TENANT_B_DOMAIN = "globex.com"
TENANT_B_SLUG = "e2e-globex"
TENANT_B_NAME = "租户 B 夹具"
TENANT_B_USER_EMAIL = "mallory@globex.com"
TENANT_B_USER_NAME = "Mallory"
TENANT_B_AGENT_ID = "globex-agent-1"
TENANT_B_AGENT_NAME = "租户 B 智能体"
TENANT_B_AGENT_DESCRIPTION = "仅租户 B 可见的智能体"
TENANT_B_DIFY_APP_ID = "globex-app-1"
TENANT_B_CONVERSATION_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")
TENANT_B_CONVERSATION_TITLE = "租户 B 会话"

# The real-stream journey registers probe agents in tenant A; the prefix lets
# the seed drop a previous run's rows without touching anything else there.
CONV_PROBE_AGENT_PREFIX = "e2e-conv01-"

# The dashboard fixture: nine of today's runs split 1/3/5 across three agents so
# the ranking order is unambiguous, every run 1200 ms so the average is exact,
# and one failed run so the error rate is 1/9. The journeys assert 9 calls,
# "1.2 s" and "11.1%" against these numbers.
#
# Insertion order deliberately ascends by call count while the identifiers
# ascend with it: a ranking rendered in natural/id order therefore reads
# 甲,乙,丙 and only a real descending sort produces 丙,乙,甲.
RANKING = (
    ("e2e-fixture-rank-a", "排行甲", 1, 0),
    ("e2e-fixture-rank-b", "排行乙", 3, 0),
    ("e2e-fixture-rank-c", "排行丙", 5, 1),
)
RUN_LATENCY_MS = 1200
# Delay on the approvable tool's upstream, so the approved task stays in
# `executing` long enough to be observed before it completes.
APPROVE_DELAY_MS = 3000

# Journey → the fixtures it needs. ``None`` (no spec) means the whole suite.
SPEC_FIXTURES: dict[str, frozenset[str]] = {
    "tc-tool-01": frozenset({"tools"}),
    "tc-tool-05": frozenset({"tools", "tasks"}),
    "tc-tool-07": frozenset({"tools", "tasks"}),
    "tc-ops-01": frozenset({"metrics"}),
    "tc-sec-01": frozenset({"cross_tenant"}),
    "tc-conv-01": frozenset({"cross_tenant"}),
}
ALL_FIXTURES = frozenset({"tools", "tasks", "metrics", "cross_tenant"})


def wanted_fixtures(spec: str | None) -> frozenset[str]:
    """Fixtures the run named by ``spec`` needs; unknown specs need nothing."""
    if not spec:
        return ALL_FIXTURES
    for key, wanted in SPEC_FIXTURES.items():
        if key in spec:
            return wanted
    return frozenset()


def _stub_base() -> str:
    """URL the *backend* uses to reach the stub upstream.

    The compose stack resolves the stub container by service name; a backend
    running on the host overrides it with the published port.
    """
    override = os.environ.get("E2E_TOOL_STUB_BASE")
    if override:
        return override
    port = os.environ.get("MOCK_IDP_PORT", "4000")
    return f"http://mock-idp:{port}/tool-stub"


def _stub_public_base() -> str:
    """URL the *test process* uses for the stub's call ledger."""
    port = os.environ.get("MOCK_IDP_PORT", "4000")
    return f"http://localhost:{port}/tool-stub"


async def _tenant_by_domain(session, domain: str) -> Tenant | None:
    return await session.scalar(select(Tenant).where(Tenant.sso_domain_folded == fold(domain)))


async def _ensure_tenant(session, domain: str, slug: str, name: str) -> Tenant:
    tenant = await _tenant_by_domain(session, domain)
    if tenant is not None:
        return tenant
    tenant = Tenant(
        name=name,
        slug=slug,
        sso_domain=domain,
        sso_domain_folded=fold(domain),
        sso_provider="local",
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def _user_by_email(session, tenant: Tenant, email: str) -> User | None:
    return await session.scalar(
        select(User).where(User.tenant_id == tenant.id, User.email_folded == fold(email))
    )


async def _ensure_user(session, tenant: Tenant, email: str, name: str, role: str, key: str) -> User:
    user = await _user_by_email(session, tenant, email)
    if user is not None:
        return user
    user = User(
        tenant_id=tenant.id,
        sso_sub=f"{LOCAL_SUBJECT_PREFIX}{key}",
        email=email,
        email_folded=fold(email),
        name=name,
        role=role,
        status="active",
        account_source=ACCOUNT_SOURCE_LOCAL,
        password_hash=hash_password(PASSWORD),
        must_change_password=False,
    )
    session.add(user)
    await session.flush()
    return user


async def _clear_tenant_run_logs(session, tenant: Tenant) -> None:
    """Drop one tenant's run logs; the trace children carry no ON DELETE CASCADE."""
    trace_ids = select(RunLog.trace_id).where(RunLog.tenant_id == tenant.id)
    for child in (TraceStep, TraceCitation, TraceToolCall):
        await session.execute(
            delete(child)
            .where(child.trace_id.in_(trace_ids))
            .execution_options(synchronize_session=False)
        )
    await session.execute(
        delete(RunLog)
        .where(RunLog.tenant_id == tenant.id)
        .execution_options(synchronize_session=False)
    )


async def _seed_tools_and_tasks(session, wanted: frozenset[str]) -> dict[str, object]:
    """Register the probe tools (and, when asked, the probe tasks) for the shared tenant."""
    tenant = await _tenant_by_domain(session, TENANT_DOMAIN)
    if tenant is None:
        raise SystemExit(f"tenant {TENANT_DOMAIN} is missing")
    admin = await _user_by_email(session, tenant, ADMIN_EMAIL)
    employee = await _user_by_email(session, tenant, EMPLOYEE_EMAIL)
    if admin is None or employee is None:
        raise SystemExit("the seeded local accounts are missing; run the account seed first")

    now = datetime.now(UTC)
    stub = _stub_base()

    await session.execute(
        delete(Task)
        .where(Task.tenant_id == tenant.id)
        .where(Task.id.in_([uuid.UUID(value[0]) for value in TASKS.values()]))
        .execution_options(synchronize_session=False)
    )
    # Tool ids are a global primary key, so the delete names the tenant too.
    await session.execute(
        delete(ToolRegistry)
        .where(ToolRegistry.tenant_id == tenant.id)
        .where(ToolRegistry.tool_id.like("e2e-%"))
        .execution_options(synchronize_session=False)
    )

    # The upstream parameters travel in the task payload, not the endpoint:
    # the proxy sends a GET's payload as the query and drops an endpoint query
    # when the payload is empty.
    for tool_id, name in (
        (TOOLS["approve"], "执行链探针工具"),
        (TOOLS["denied"], "拒绝链探针工具"),
    ):
        session.add(
            ToolRegistry(
                tool_id=tool_id,
                tenant_id=tenant.id,
                name=name,
                description="浏览器验收夹具",
                type="http",
                endpoint=stub,
                method="GET",
                risk_level="high",
                permission_mode="confirm",
                auth_type="none",
                timeout_ms=15000,
                retry_policy={"max_retries": 0, "base_delay_ms": 0},
                status="active",
                created_by=admin.id,
            )
        )

    def task(
        key: str,
        *,
        status: str,
        creator: User,
        expires_at: datetime | None,
        tool_id: str,
        tool_name: str,
        result: dict[str, object] | None = None,
        params: dict[str, object] | None = None,
    ) -> Task:
        task_id, title = TASKS[key]
        payload: dict[str, object] = {
            "tool_id": tool_id,
            "tool_name": tool_name,
            "params": params or {},
            "conversation_id": None,
        }
        return Task(
            id=uuid.UUID(task_id),
            tenant_id=tenant.id,
            creator_id=creator.id,
            type="tool_approval",
            title=title,
            priority="high",
            status=status,
            payload=payload,
            result=result,
            expires_at=expires_at,
            resolved_at=None if status == "pending" else now,
        )

    manifest: dict[str, object] = {"tools": dict(TOOLS)}
    if "tasks" in wanted:
        approve_tool = "执行链探针工具"
        denied_tool = "拒绝链探针工具"
        # 24h timeout (C-75): inserted still `pending` with a deadline that has
        # already passed, so the 15s expiry scan is what retires it — the journey
        # asserts the cancelled row, not a row seeded as cancelled.
        session.add_all(
            [
                task(
                    "approve",
                    status="pending",
                    creator=employee,
                    expires_at=now + timedelta(hours=24),
                    tool_id=TOOLS["approve"],
                    tool_name=approve_tool,
                    params={"marker": "e2e-approve-flow", "delay_ms": APPROVE_DELAY_MS},
                ),
                task(
                    "timeout",
                    status="pending",
                    creator=employee,
                    expires_at=now - timedelta(hours=24),
                    tool_id=TOOLS["denied"],
                    tool_name=denied_tool,
                    params={"marker": "e2e-denied"},
                ),
                task(
                    "reject",
                    status="pending",
                    creator=employee,
                    expires_at=now + timedelta(hours=24),
                    tool_id=TOOLS["denied"],
                    tool_name=denied_tool,
                    params={"marker": "e2e-denied"},
                ),
                task(
                    "rejected",
                    status="rejected",
                    creator=employee,
                    expires_at=now + timedelta(hours=24),
                    tool_id=TOOLS["denied"],
                    tool_name=denied_tool,
                    result={"reason": "风险过高，不予放行"},
                    params={"marker": "e2e-denied"},
                ),
                task(
                    "cancelled",
                    status="cancelled",
                    creator=employee,
                    expires_at=now + timedelta(hours=24),
                    tool_id=TOOLS["denied"],
                    tool_name=denied_tool,
                    result={"reason": "用户取消"},
                    params={"marker": "e2e-denied"},
                ),
            ]
        )
        manifest["tasks"] = {
            key: {
                "id": value[0],
                "title": value[1],
                "seededStatus": TASK_SEEDED_STATUS[key],
            }
            for key, value in TASKS.items()
        }

    return manifest


async def _seed_metrics(session) -> dict[str, object]:
    """Rebuild today's run logs inside the dedicated metrics tenant."""
    tenant = await _ensure_tenant(session, METRICS_DOMAIN, METRICS_SLUG, METRICS_TENANT_NAME)
    admin = await _ensure_user(
        session, tenant, METRICS_ADMIN_EMAIL, METRICS_ADMIN_NAME, "agent_admin", "metrics-admin"
    )

    # Scoped to this tenant only: the fixture owns every row it deletes.
    await _clear_tenant_run_logs(session, tenant)
    await session.execute(
        delete(AgentRegistry)
        .where(AgentRegistry.tenant_id == tenant.id)
        .where(AgentRegistry.agent_id.like("e2e-fixture-%"))
        .execution_options(synchronize_session=False)
    )

    now = datetime.now(UTC)
    for agent_id, name, _calls, _failures in RANKING:
        session.add(
            AgentRegistry(
                tenant_id=tenant.id,
                agent_id=agent_id,
                dify_app_id=f"e2e-fixture-{agent_id}",
                name=name,
                type="chat",
                status="published",
                created_by=admin.id,
            )
        )
    # The agent relationship on a run log is view-only, so the unit of work has
    # no edge that would order these inserts; flush the agents explicitly.
    await session.flush()

    for agent_id, name, calls, failures in RANKING:
        for index in range(calls):
            session.add(
                RunLog(
                    id=uuid.uuid4(),
                    trace_id=f"e2e-fixture-{agent_id}-{index}",
                    tenant_id=tenant.id,
                    agent_id=agent_id,
                    user_id=admin.id,
                    agent_name=name,
                    user_name=admin.name,
                    status="failed" if index < failures else "success",
                    input="fixture",
                    output="fixture",
                    latency_ms=RUN_LATENCY_MS,
                    created_at=now,
                )
            )

    total_calls = sum(calls for _, _, calls, _ in RANKING)
    failed_calls = sum(failures for _, _, _, failures in RANKING)
    error_rate = failed_calls / total_calls

    return {
        "dashboard": {
            "accountEmail": METRICS_ADMIN_EMAIL,
            "todayCalls": total_calls,
            "avgLatencyMs": RUN_LATENCY_MS,
            "errorRate": error_rate,
            "successRate": 1 - error_rate,
            "ranking": [
                {"name": name, "calls": calls}
                for _, name, calls, _ in sorted(RANKING, key=lambda item: item[2], reverse=True)
            ],
        }
    }


async def _reset_tenant_probe(
    session,
    *,
    tenant: Tenant,
    admin: User,
    agent_id: str,
    agent_name: str,
    agent_description: str,
    dify_app_id: str,
    conversation_id: uuid.UUID,
    conversation_title: str,
) -> None:
    """Replace one tenant's published agent and conversation with fixed rows."""
    await session.execute(
        delete(Conversation)
        .where(Conversation.tenant_id == tenant.id)
        .where(Conversation.id == conversation_id)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(AgentRegistry)
        .where(AgentRegistry.tenant_id == tenant.id)
        .where(AgentRegistry.agent_id == agent_id)
        .execution_options(synchronize_session=False)
    )
    session.add(
        AgentRegistry(
            tenant_id=tenant.id,
            agent_id=agent_id,
            dify_app_id=dify_app_id,
            name=agent_name,
            description=agent_description,
            type="chat",
            tags=[],
            status="published",
            version=1,
            created_by=admin.id,
        )
    )
    await session.flush()
    session.add(
        Conversation(
            id=conversation_id,
            tenant_id=tenant.id,
            user_id=admin.id,
            agent_id=agent_id,
            agent_name=agent_name,
            title=conversation_title,
            status="active",
        )
    )


async def _seed_cross_tenant(session) -> dict[str, object]:
    """Rebuild the two dedicated tenants the isolation journey reads.

    Each side owns one published agent and one conversation, so tenant A's list
    has a positive row to show beside the absence of tenant B's. The shared acme
    tenant stays untouched. Row ids are fixed so the seed replaces only its own
    rows, and the real-stream probes of a previous run are dropped by prefix.
    """
    tenant_a = await _ensure_tenant(
        session, TENANT_A_DOMAIN, TENANT_A_SLUG, TENANT_A_NAME
    )
    admin_a = await _ensure_user(
        session,
        tenant_a,
        TENANT_A_USER_EMAIL,
        TENANT_A_USER_NAME,
        "agent_admin",
        "initech-admin",
    )
    tenant_b = await _ensure_tenant(
        session, TENANT_B_DOMAIN, TENANT_B_SLUG, TENANT_B_NAME
    )
    admin_b = await _ensure_user(
        session,
        tenant_b,
        TENANT_B_USER_EMAIL,
        TENANT_B_USER_NAME,
        "agent_admin",
        "globex-admin",
    )

    # The real-stream journey registers throwaway agents in tenant A; drop a
    # previous run's rows (and their conversations and run logs) by prefix. The
    # run log carries the composite FK, so it goes first.
    await _clear_tenant_run_logs(session, tenant_a)
    probe_ids = (
        select(AgentRegistry.agent_id)
        .where(AgentRegistry.tenant_id == tenant_a.id)
        .where(AgentRegistry.agent_id.like(f"{CONV_PROBE_AGENT_PREFIX}%"))
    )
    await session.execute(
        delete(Conversation)
        .where(Conversation.tenant_id == tenant_a.id)
        .where(Conversation.agent_id.in_(probe_ids))
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(AgentRegistry)
        .where(AgentRegistry.tenant_id == tenant_a.id)
        .where(AgentRegistry.agent_id.like(f"{CONV_PROBE_AGENT_PREFIX}%"))
        .execution_options(synchronize_session=False)
    )

    await _reset_tenant_probe(
        session,
        tenant=tenant_a,
        admin=admin_a,
        agent_id=TENANT_A_AGENT_ID,
        agent_name=TENANT_A_AGENT_NAME,
        agent_description=TENANT_A_AGENT_DESCRIPTION,
        dify_app_id=TENANT_A_DIFY_APP_ID,
        conversation_id=TENANT_A_CONVERSATION_ID,
        conversation_title=TENANT_A_CONVERSATION_TITLE,
    )
    await _reset_tenant_probe(
        session,
        tenant=tenant_b,
        admin=admin_b,
        agent_id=TENANT_B_AGENT_ID,
        agent_name=TENANT_B_AGENT_NAME,
        agent_description=TENANT_B_AGENT_DESCRIPTION,
        dify_app_id=TENANT_B_DIFY_APP_ID,
        conversation_id=TENANT_B_CONVERSATION_ID,
        conversation_title=TENANT_B_CONVERSATION_TITLE,
    )
    return {
        "crossTenant": {
            "tenantA": {
                "email": TENANT_A_USER_EMAIL,
                "agentId": TENANT_A_AGENT_ID,
                "agentName": TENANT_A_AGENT_NAME,
                "conversationId": str(TENANT_A_CONVERSATION_ID),
                "conversationTitle": TENANT_A_CONVERSATION_TITLE,
            },
            "tenantB": {
                "email": TENANT_B_USER_EMAIL,
                "agentId": TENANT_B_AGENT_ID,
                "agentName": TENANT_B_AGENT_NAME,
                "agentDescription": TENANT_B_AGENT_DESCRIPTION,
                "difyAppId": TENANT_B_DIFY_APP_ID,
                "conversationId": str(TENANT_B_CONVERSATION_ID),
                "conversationTitle": TENANT_B_CONVERSATION_TITLE,
            },
        }
    }


async def _seed(session, wanted: frozenset[str]) -> dict[str, object]:
    manifest: dict[str, object] = {"seeded": sorted(wanted)}
    if wanted & {"tools", "tasks"}:
        manifest.update(await _seed_tools_and_tasks(session, wanted))
        manifest["stub"] = {
            "publicUrl": _stub_public_base(),
            "approveMarker": "e2e-approve-flow",
            "deniedMarker": "e2e-denied",
            "approveDelayMs": APPROVE_DELAY_MS,
        }
    if "metrics" in wanted:
        manifest.update(await _seed_metrics(session))
    if "cross_tenant" in wanted:
        manifest.update(await _seed_cross_tenant(session))
    await session.commit()
    return manifest


async def main() -> None:
    wanted = wanted_fixtures(os.environ.get("E2E_FIXTURE_SPEC"))
    async with async_session_factory() as session:
        # Serialize against the stack's bootstrap and the account seed; the
        # seed's own writes commit at the end of _seed.
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('eap:e2e:fixtures'))"))
        manifest = await _seed(session, wanted)
    json.dump(manifest, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    print(f"e2e: fixtures ready ({', '.join(sorted(wanted)) or 'none'})", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
