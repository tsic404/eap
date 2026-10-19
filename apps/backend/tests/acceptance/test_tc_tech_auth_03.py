"""Design acceptance case TC-tech-auth-03.

Negative tenant isolation: another tenant's resources are invisible, take the
domain's fixed not-found answer, and never leak a single field; the tenant
context comes from the access token, and an employee only ever sees published
agents.

Only the platform's own HTTP surface is observed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from app.models.run_log import RunLog
from app.models.tenant import Tenant
from app.models.user import User

# The imports above must precede every other statement; the case identifier
# leads the module body from here on.
TC_ID = "TC-tech-auth-03"

_AGENTS = "/api/agents"
_TOOLS = "/api/tools"
_KNOWLEDGE_BASES = "/api/knowledge-bases"
_TASKS = "/api/tasks"
_RUN_LOGS = "/api/run-logs"


# ── helpers ─────────────────────────────────────────────────────────────────


def _sign(api: Any, user: User, tenant: Tenant | None, *, role: str | None = None) -> str:
    """Sign an access token; a ``None`` tenant omits the tenant claim."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "role": role or user.role,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(seconds=900),
    }
    if tenant is not None:
        payload["tenantId"] = str(tenant.id)
    return jwt.encode(payload, api.private_key, algorithm="RS256")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _agent_ids(response: Any) -> set[str]:
    assert response.status_code == 200, response.text
    return {item["agentId"] for item in response.json()["data"]["items"]}


def _tool_ids(response: Any) -> set[str]:
    assert response.status_code == 200, response.text
    return {item["tool_id"] for item in response.json()["data"]}


async def _seed_run_log(
    api: Any, tenant: Tenant, user: User, *, trace_id: str, agent_id: str
) -> None:
    """Seed one run log of ``tenant``; the trace identifier is its public key."""
    async with api.db.session() as session:
        session.add(
            RunLog(
                trace_id=trace_id,
                tenant_id=tenant.id,
                agent_id=agent_id,
                user_id=user.id,
                status="success",
                tool_call_count=0,
                knowledge_hit_count=0,
            )
        )
        await session.commit()


# ── C-14 / C-102: lists are tenant scoped ──────────────────────────────────


@pytest.mark.asyncio
async def test_tenant_agent_and_tool_lists_are_disjoint(api) -> None:
    tenant_a, admin_a = await api.seed(role="agent_admin")
    tenant_b, admin_b = await api.seed(role="agent_admin")
    await api.seed_agent(tenant_a, agent_id="a-agent")
    await api.seed_agent(tenant_b, agent_id="b-agent")
    await api.seed_tool(tenant_a, tool_id="a-tool")
    await api.seed_tool(tenant_b, tool_id="b-tool")

    agents_a = _agent_ids(await api.client.get(_AGENTS, headers=api.auth(admin_a, tenant_a)))
    agents_b = _agent_ids(await api.client.get(_AGENTS, headers=api.auth(admin_b, tenant_b)))
    assert agents_a == {"a-agent"}
    assert agents_b == {"b-agent"}
    assert agents_a.isdisjoint(agents_b)

    tools_a = _tool_ids(await api.client.get(_TOOLS, headers=api.auth(admin_a, tenant_a)))
    tools_b = _tool_ids(await api.client.get(_TOOLS, headers=api.auth(admin_b, tenant_b)))
    assert tools_a == {"a-tool"}
    assert tools_b == {"b-tool"}
    assert tools_a.isdisjoint(tools_b)


# ── C-14 / C-109: by-id reads of another tenant's resource ─────────────────


@pytest.mark.asyncio
async def test_cross_tenant_agent_detail_is_not_found_without_leaking_fields(api) -> None:
    tenant_a, admin_a = await api.seed(role="agent_admin")
    tenant_b, _ = await api.seed(role="agent_admin")
    await api.seed_agent(tenant_b, agent_id="b-agent")
    headers = api.auth(admin_a, tenant_a)

    other_tenant = await api.client.get(f"{_AGENTS}/b-agent", headers=headers)
    never_existed = await api.client.get(f"{_AGENTS}/no-such-agent", headers=headers)

    assert other_tenant.status_code == 404, other_tenant.text
    # The two answers are indistinguishable: no 403, no oracle on existence.
    assert other_tenant.json() == never_existed.json()
    # Neither the resource identity nor its upstream application material leaks.
    assert "b-agent" not in other_tenant.text
    assert "dify-b-agent" not in other_tenant.text
    assert "app-secret" not in other_tenant.text


@pytest.mark.asyncio
async def test_soft_deleted_agent_detail_matches_a_missing_resource(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="gone-agent")
    headers = api.auth(admin, tenant)

    deleted = await api.client.delete(f"{_AGENTS}/gone-agent", headers=headers)
    assert deleted.status_code == 204, deleted.text

    after_delete = await api.client.get(f"{_AGENTS}/gone-agent", headers=headers)
    never_existed = await api.client.get(f"{_AGENTS}/never-existed", headers=headers)
    assert after_delete.status_code == 404, after_delete.text
    assert after_delete.json() == never_existed.json()


@pytest.mark.asyncio
async def test_cross_tenant_tool_detail_is_not_found_without_leaking_fields(api) -> None:
    tenant_a, admin_a = await api.seed(role="agent_admin")
    tenant_b, _ = await api.seed(role="agent_admin")
    await api.seed_tool(tenant_b, tool_id="b-tool", name="Secret Tool")
    headers = api.auth(admin_a, tenant_a)

    other_tenant = await api.client.get(f"{_TOOLS}/b-tool", headers=headers)
    never_existed = await api.client.get(f"{_TOOLS}/no-such-tool", headers=headers)

    assert other_tenant.status_code == 404, other_tenant.text
    assert other_tenant.json() == never_existed.json()
    assert "b-tool" not in other_tenant.text
    assert "Secret Tool" not in other_tenant.text
    assert "tool.example" not in other_tenant.text


@pytest.mark.asyncio
async def test_cross_tenant_knowledge_base_detail_is_not_found_without_leaking_fields(api) -> None:
    tenant_a, admin_a = await api.seed(role="agent_admin")
    tenant_b, _ = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant_b, kb_id="b-kb")
    headers = api.auth(admin_a, tenant_a)

    other_tenant = await api.client.get(f"{_KNOWLEDGE_BASES}/b-kb", headers=headers)
    never_existed = await api.client.get(f"{_KNOWLEDGE_BASES}/no-such-kb", headers=headers)

    assert other_tenant.status_code == 404, other_tenant.text
    assert other_tenant.json() == never_existed.json()
    # C-109: neither the knowledge base nor its private upstream material shows.
    assert "b-kb" not in other_tenant.text
    assert "ds-b-kb" not in other_tenant.text
    assert "ds-secret" not in other_tenant.text


@pytest.mark.asyncio
async def test_cross_tenant_task_detail_is_not_found_without_leaking_fields(api) -> None:
    tenant_a, admin_a = await api.seed(role="agent_admin")
    tenant_b, admin_b = await api.seed(role="agent_admin")
    task = await api.seed_task(tenant_b, admin_b)
    headers = api.auth(admin_a, tenant_a)

    other_tenant = await api.client.get(f"{_TASKS}/{task.id}", headers=headers)
    never_existed = await api.client.get(f"{_TASKS}/{uuid.uuid4()}", headers=headers)

    assert other_tenant.status_code == 404, other_tenant.text
    assert other_tenant.json() == never_existed.json()
    # The task's own title and payload stay private to its tenant.
    assert "Approve tool" not in other_tenant.text
    assert "tool-1" not in other_tenant.text


@pytest.mark.asyncio
async def test_cross_tenant_run_log_detail_is_not_found_without_leaking_fields(api) -> None:
    tenant_a, admin_a = await api.seed(role="auditor")
    tenant_b, admin_b = await api.seed(role="auditor")
    await api.seed_agent(tenant_b, agent_id="b-agent")
    await _seed_run_log(api, tenant_b, admin_b, trace_id="b-trace", agent_id="b-agent")
    headers = api.auth(admin_a, tenant_a)

    other_tenant = await api.client.get(f"{_RUN_LOGS}/b-trace", headers=headers)
    never_existed = await api.client.get(f"{_RUN_LOGS}/no-such-trace", headers=headers)

    assert other_tenant.status_code == 404, other_tenant.text
    assert other_tenant.json() == never_existed.json()
    assert "b-trace" not in other_tenant.text
    assert "b-agent" not in other_tenant.text


# ── C-13: what supplies the tenant context ─────────────────────────────────


@pytest.mark.asyncio
async def test_non_platform_admin_cannot_switch_to_another_tenant(api) -> None:
    tenant_a, admin_a = await api.seed(role="agent_admin")
    tenant_b, _ = await api.seed(role="agent_admin")
    token = _sign(api, admin_a, None)

    other_tenant = await api.client.get(
        _AGENTS, headers={**_bearer(token), "X-Tenant-Id": str(tenant_b.id)}
    )
    absent_tenant = await api.client.get(
        _AGENTS, headers={**_bearer(token), "X-Tenant-Id": str(uuid.uuid4())}
    )

    assert other_tenant.status_code == 403, other_tenant.text
    assert _code(other_tenant) == "FORBIDDEN"
    # An existing and a non-existent tenant are refused identically, so the
    # answer says nothing about whether the tenant exists.
    assert other_tenant.json() == absent_tenant.json()


@pytest.mark.asyncio
async def test_tenant_context_follows_the_access_token_over_the_header(api) -> None:
    tenant_a, admin_a = await api.seed(role="agent_admin")
    tenant_b, _ = await api.seed(role="agent_admin")
    await api.seed_agent(tenant_a, agent_id="a-agent")
    await api.seed_agent(tenant_b, agent_id="b-agent")

    response = await api.client.get(
        _AGENTS, headers={**api.auth(admin_a, tenant_a), "X-Tenant-Id": str(tenant_b.id)}
    )
    assert response.status_code == 200, response.text
    assert _agent_ids(response) == {"a-agent"}


@pytest.mark.asyncio
async def test_malformed_tenant_header_is_unauthorized(api) -> None:
    _, admin = await api.seed(role="agent_admin")
    token = _sign(api, admin, None)
    response = await api.client.get(
        _AGENTS, headers={**_bearer(token), "X-Tenant-Id": "not-a-tenant-id"}
    )
    assert response.status_code == 401, response.text
    assert _code(response) == "UNAUTHORIZED"


# ── C-34: published-agent visibility for non-admins ─────────────────────────


@pytest.mark.asyncio
async def test_employee_list_contains_only_published_agents(api) -> None:
    tenant, employee = await api.seed(role="employee")
    await api.seed_agent(tenant, agent_id="live-agent", status="published")
    await api.seed_agent(tenant, agent_id="draft-agent", status="draft")
    headers = api.auth(employee, tenant)

    listing = await api.client.get(_AGENTS, headers=headers)
    assert _agent_ids(listing) == {"live-agent"}

    # The requested status filter is ignored for a non-admin, so asking for
    # drafts still yields exactly the published set — and never the draft.
    filtered = await api.client.get(_AGENTS, headers=headers, params={"status": "draft"})
    assert _agent_ids(filtered) == {"live-agent"}


@pytest.mark.asyncio
async def test_employee_cannot_read_an_unpublished_agent(api) -> None:
    tenant, employee = await api.seed(role="employee")
    await api.seed_agent(tenant, agent_id="draft-agent", status="draft")
    headers = api.auth(employee, tenant)

    unpublished = await api.client.get(f"{_AGENTS}/draft-agent", headers=headers)
    never_existed = await api.client.get(f"{_AGENTS}/never-existed", headers=headers)

    assert unpublished.status_code == 404, unpublished.text
    assert unpublished.json() == never_existed.json()
