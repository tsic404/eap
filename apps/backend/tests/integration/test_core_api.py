"""32 core API integration test cases (architecture doc §31.1.2 / §33.7.2).

Every case drives the full ASGI stack against a real PostgreSQL + Redis
(testcontainers); only the Dify boundary is mocked. The suite is the
integration layer of the test pyramid — unit tests live in ``tests/`` and cover
service/repository internals.
"""

from __future__ import annotations

import json

import pytest
from rq import Queue

from app.dify_console import DifyConsoleError

# ── auth / me ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_me_returns_current_user(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    resp = await api.client.get("/api/me", headers=api.auth(user, tenant))
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["email"] == user.email
    assert body["role"] == "agent_admin"
    assert str(body["id"]) == str(user.id)


@pytest.mark.asyncio
async def test_me_without_token_returns_401(api) -> None:
    resp = await api.client.get("/api/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


# ── agents ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_agent_returns_201_draft(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    resp = await api.client.post(
        "/api/agents",
        headers=api.auth(user, tenant),
        json={"agentId": "sales-bot", "name": "Sales Bot"},
    )
    assert resp.status_code == 201
    body = resp.json()["data"]
    assert body["agentId"] == "sales-bot"
    assert body["status"] == "draft"
    assert body["difyAppId"] == "dify-app-1"


@pytest.mark.asyncio
async def test_create_agent_duplicate_id_returns_409(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    payload = {"agentId": "dup-bot", "name": "Bot"}
    first = await api.client.post("/api/agents", headers=headers, json=payload)
    assert first.status_code == 201
    second = await api.client.post("/api/agents", headers=headers, json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DUPLICATE_AGENT_ID"


@pytest.mark.asyncio
async def test_create_agent_employee_returns_403(api) -> None:
    tenant, user = await api.seed(role="employee")
    resp = await api.client.post(
        "/api/agents",
        headers=api.auth(user, tenant),
        json={"agentId": "forbidden-bot", "name": "Bot"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_list_agents_isolated_by_tenant(api) -> None:
    tenant_a, user_a = await api.seed(role="agent_admin")
    tenant_b, user_b = await api.seed(role="agent_admin")
    await api.seed_agent(tenant_a, agent_id="a-bot")

    resp_a = await api.client.get("/api/agents", headers=api.auth(user_a, tenant_a))
    resp_b = await api.client.get("/api/agents", headers=api.auth(user_b, tenant_b))

    assert resp_a.status_code == 200
    assert [item["agentId"] for item in resp_a.json()["data"]["items"]] == ["a-bot"]
    assert resp_a.json()["data"]["total"] == 1
    assert resp_b.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_list_agents_non_admin_sees_only_published(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="published-bot", status="published")
    await api.seed_agent(tenant, agent_id="draft-bot", status="draft")
    employee = await api.seed_user(tenant, role="employee")

    resp = await api.client.get("/api/agents", headers=api.auth(employee, tenant))

    assert resp.status_code == 200
    ids = [item["agentId"] for item in resp.json()["data"]["items"]]
    assert ids == ["published-bot"]


@pytest.mark.asyncio
async def test_update_agent_bumps_version(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="bot-1", status="draft")

    resp = await api.client.patch(
        "/api/agents/bot-1",
        headers=api.auth(user, tenant),
        json={"version": 1, "name": "Renamed"},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["name"] == "Renamed"
    assert body["version"] == 2


@pytest.mark.asyncio
async def test_update_agent_stale_version_returns_409(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="bot-1", status="draft")

    resp = await api.client.patch(
        "/api/agents/bot-1",
        headers=api.auth(user, tenant),
        json={"version": 99, "name": "Stale"},
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_publish_agent_sets_published(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="bot-1", status="testing")

    resp = await api.client.post(
        "/api/agents/bot-1/publish",
        headers=api.auth(user, tenant),
        json={"version": 1},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "published"
    assert body["publishedAt"] is not None


@pytest.mark.asyncio
async def test_offline_agent_sets_offline(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="bot-1", status="published")

    resp = await api.client.post(
        "/api/agents/bot-1/offline",
        headers=api.auth(user, tenant),
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "offline"


# ── knowledge-bases ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_knowledge_base_returns_201(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    resp = await api.client.post(
        "/api/knowledge-bases",
        headers=api.auth(user, tenant),
        json={"name": "KB", "description": "docs"},
    )
    assert resp.status_code == 201
    body = resp.json()["data"]
    assert body["name"] == "KB"
    assert body["kb_id"]


@pytest.mark.asyncio
async def test_create_knowledge_base_employee_returns_403(api) -> None:
    tenant, user = await api.seed(role="employee")
    resp = await api.client.post(
        "/api/knowledge-bases",
        headers=api.auth(user, tenant),
        json={"name": "KB"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_list_knowledge_bases_returns_total(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")

    resp = await api.client.get("/api/knowledge-bases", headers=api.auth(user, tenant))

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["kb_id"] == "kb-1"


@pytest.mark.asyncio
async def test_upload_pdf_returns_201_indexing(api, wait_for_indexed) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)

    resp = await api.client.post(
        "/api/knowledge-bases/kb-1/documents",
        headers=headers,
        files={"file": ("report.pdf", b"%PDF-1.7 content", "application/pdf")},
    )

    assert resp.status_code == 201
    assert resp.json()["data"] == {"id": "doc-1", "name": "report.pdf", "status": "indexing"}

    # Exercise the waitForIndexed helper: poll the status endpoint until the
    # Dify mock reports a terminal indexing status.
    status = await wait_for_indexed(api.client, headers, "kb-1", "doc-1")
    assert status == "completed"


@pytest.mark.asyncio
async def test_status_poll_records_completion_and_chunk_count(api, wait_for_indexed) -> None:
    """Direct status polling also records the completion and the segment count."""
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)

    uploaded = await api.client.post(
        "/api/knowledge-bases/kb-1/documents",
        headers=headers,
        files={"file": ("kb-sample.txt", b"kb sample body", "text/plain")},
    )
    assert uploaded.status_code == 201
    assert await wait_for_indexed(api.client, headers, "kb-1", "doc-1") == "completed"

    listed = await api.client.get("/api/knowledge-bases", headers=headers)
    # The Dify mock reports 5 segments for the document.
    assert listed.json()["data"]["items"][0]["chunk_count"] == 5

    # Re-polling a finished document must not add its segments twice.
    await api.client.get("/api/knowledge-bases/kb-1/documents/doc-1/status", headers=headers)
    again = await api.client.get("/api/knowledge-bases", headers=headers)
    assert again.json()["data"]["items"][0]["chunk_count"] == 5


@pytest.mark.asyncio
async def test_product_flow_document_listing_drives_kb_chunk_count(api) -> None:
    """UI flow: upload → poll the document listing → the KB reports the count.

    The frontend polls ``GET /knowledge-bases/{id}/documents`` every 3s while
    indexing and never calls the per-document status endpoint, so this test
    deliberately never polls status: it fails if the listing does not record the
    terminal status itself.
    """
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)

    uploaded = await api.client.post(
        "/api/knowledge-bases/kb-1/documents",
        headers=headers,
        files={"file": ("kb-sample.txt", b"kb sample body", "text/plain")},
    )
    assert uploaded.status_code == 201

    # Dify finished indexing; the UI's next listing poll is what observes it.
    api.console.list_documents.return_value = {
        "data": [{"id": "doc-1", "name": "kb-sample.txt", "indexing_status": "completed"}],
        "total": 1,
    }
    listed = await api.client.get("/api/knowledge-bases/kb-1/documents", headers=headers)
    assert listed.json()["data"]["items"][0]["status"] == "completed"

    kb = await api.client.get("/api/knowledge-bases", headers=headers)
    assert kb.json()["data"]["items"][0]["chunk_count"] == 5

    # The next poll of the listing must not add the segments again.
    await api.client.get("/api/knowledge-bases/kb-1/documents", headers=headers)
    again = await api.client.get("/api/knowledge-bases", headers=headers)
    assert again.json()["data"]["items"][0]["chunk_count"] == 5


@pytest.mark.asyncio
async def test_upload_exe_returns_422_unsupported(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")

    resp = await api.client.post(
        "/api/knowledge-bases/kb-1/documents",
        headers=api.auth(user, tenant),
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNSUPPORTED_FORMAT"


@pytest.mark.asyncio
async def test_delete_document_returns_204_and_releases_kb_counts(api, wait_for_indexed) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await api.client.post(
        "/api/knowledge-bases/kb-1/documents",
        headers=headers,
        files={"file": ("report.pdf", b"%PDF-1.7 content", "application/pdf")},
    )
    # Let the document finish indexing first: the KB then carries its document
    # and its segments, and the delete has to take both back out.
    assert await wait_for_indexed(api.client, headers, "kb-1", "doc-1") == "completed"
    counted = await api.client.get("/api/knowledge-bases/kb-1", headers=headers)
    assert counted.json()["data"]["doc_count"] == 1
    assert counted.json()["data"]["chunk_count"] == 5

    resp = await api.client.delete("/api/knowledge-bases/kb-1/documents/doc-1", headers=headers)

    assert resp.status_code == 204
    api.console.delete_document.assert_awaited_once_with("ds-kb-1", "doc-1")
    detail = await api.client.get("/api/knowledge-bases/kb-1", headers=headers)
    assert detail.json()["data"]["doc_count"] == 0
    assert detail.json()["data"]["chunk_count"] == 0


@pytest.mark.asyncio
async def test_delete_document_reclaims_nothing_when_completion_is_unobserved(api) -> None:
    """A document Dify finished but the platform never recorded as completed.

    The KB total only grows on a recorded terminal transition, so deleting a
    document whose completion was never observed must leave ``chunk_count``
    untouched — the eligibility check reads the local row, not Dify.
    """
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    uploaded = await api.client.post(
        "/api/knowledge-bases/kb-1/documents",
        headers=headers,
        files={"file": ("report.pdf", b"%PDF-1.7 content", "application/pdf")},
    )
    assert uploaded.status_code == 201

    # Dify would report the document as completed with 5 segments, but the
    # platform never polls it (no listing, no status call): the local row is
    # still ``indexing`` and nothing was ever added to the KB total.
    resp = await api.client.delete("/api/knowledge-bases/kb-1/documents/doc-1", headers=headers)

    assert resp.status_code == 204
    api.console.get_document_indexing_status.assert_not_awaited()
    detail = await api.client.get("/api/knowledge-bases/kb-1", headers=headers)
    assert detail.json()["data"]["doc_count"] == 0
    assert detail.json()["data"]["chunk_count"] == 0


@pytest.mark.asyncio
async def test_delete_document_while_indexing_returns_400(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await api.client.post(
        "/api/knowledge-bases/kb-1/documents",
        headers=headers,
        files={"file": ("report.pdf", b"%PDF-1.7 content", "application/pdf")},
    )
    api.console.delete_document.side_effect = DifyConsoleError(
        400,
        json.dumps(
            {
                "code": "document_indexing_error",
                "message": "Cannot delete document during indexing.",
            }
        ),
    )

    resp = await api.client.delete("/api/knowledge-bases/kb-1/documents/doc-1", headers=headers)

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "Cannot delete document during indexing."
    detail = await api.client.get("/api/knowledge-bases/kb-1", headers=headers)
    assert detail.json()["data"]["doc_count"] == 1


@pytest.mark.asyncio
async def test_document_mutations_hide_foreign_tenant_kb(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    other_tenant, _ = await api.seed(role="knowledge_admin", suffix="other")
    await api.seed_kb(tenant, kb_id="kb-2")
    await api.seed_kb(other_tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)

    retried = await api.client.post(
        "/api/knowledge-bases/kb-1/documents/doc-1/retry", headers=headers
    )
    deleted = await api.client.delete("/api/knowledge-bases/kb-1/documents/doc-1", headers=headers)

    assert retried.status_code == 404
    assert deleted.status_code == 404
    assert retried.json()["error"]["code"] == "KB_NOT_FOUND"
    api.console.retry_document_indexing.assert_not_awaited()
    api.console.delete_document.assert_not_awaited()


# ── conversations ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_conversation_returns_201(api) -> None:
    tenant, user = await api.seed(role="employee")
    await api.seed_agent(tenant, agent_id="agent-1", status="published")

    resp = await api.client.post(
        "/api/conversations",
        headers=api.auth(user, tenant),
        json={"agentId": "agent-1", "title": "First"},
    )

    assert resp.status_code == 201
    body = resp.json()["data"]
    assert body["agentId"] == "agent-1"
    assert body["title"] == "First"


@pytest.mark.asyncio
async def test_list_conversations_isolated_by_user(api) -> None:
    tenant, alice = await api.seed(role="employee")
    await api.seed_agent(tenant, agent_id="agent-1", status="published")
    bob = await api.seed_user(tenant, role="employee")

    await api.client.post(
        "/api/conversations",
        headers=api.auth(alice, tenant),
        json={"agentId": "agent-1"},
    )
    resp = await api.client.get(
        "/api/conversations", headers=api.auth(bob, tenant)
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_delete_conversation_returns_204(api) -> None:
    tenant, user = await api.seed(role="employee")
    await api.seed_agent(tenant, agent_id="agent-1", status="published")

    created = await api.client.post(
        "/api/conversations",
        headers=api.auth(user, tenant),
        json={"agentId": "agent-1"},
    )
    conv_id = created.json()["data"]["id"]

    deleted = await api.client.delete(
        f"/api/conversations/{conv_id}", headers=api.auth(user, tenant)
    )
    assert deleted.status_code == 204

    listed = await api.client.get("/api/conversations", headers=api.auth(user, tenant))
    assert listed.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_send_message_invalid_query_returns_422(api) -> None:
    tenant, user = await api.seed(role="employee")
    await api.seed_agent(tenant, agent_id="agent-1", status="published")

    resp = await api.client.post(
        "/api/conversations/00000000-0000-0000-0000-000000000000/messages",
        headers=api.auth(user, tenant),
        json={"query": "", "agentId": "agent-1"},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ── tasks ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_task_returns_approved_and_enqueues(api, wait_for_job) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    task = await api.seed_task(tenant, admin, status="pending")

    resp = await api.client.post(
        f"/api/tasks/{task.id}/approve",
        headers=api.auth(admin, tenant),
        json={"comment": "ok"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "approved"
    assert len(api.enqueued) == 1
    outbox_id = api.enqueued[0]["outbox_id"]
    job = await wait_for_job(Queue("tool-approval", connection=api.redis_conn), outbox_id)
    assert job is not None


@pytest.mark.asyncio
async def test_approve_task_invalid_transition_returns_422(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    task = await api.seed_task(tenant, admin, status="completed")

    resp = await api.client.post(
        f"/api/tasks/{task.id}/approve",
        headers=api.auth(admin, tenant),
        json={},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TRANSITION"


@pytest.mark.asyncio
async def test_approve_task_employee_returns_403(api) -> None:
    tenant, employee = await api.seed(role="employee")
    task = await api.seed_task(tenant, employee, status="pending")

    resp = await api.client.post(
        f"/api/tasks/{task.id}/approve",
        headers=api.auth(employee, tenant),
        json={},
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_list_tasks_returns_paginated(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    await api.seed_task(tenant, user, status="pending")

    resp = await api.client.get("/api/tasks", headers=api.auth(user, tenant))

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "pending"


# ── tools ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_tool_returns_201_active(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    resp = await api.client.post(
        "/api/tools",
        headers=api.auth(user, tenant),
        json={
            "name": "Contract Check",
            "tool_id": "contract-check",
            "type": "http",
            "endpoint": "http://erp.example/api",
            "risk_level": "low",
            "permission_mode": "auto",
        },
    )
    assert resp.status_code == 201
    body = resp.json()["data"]
    assert body["status"] == "active"
    assert body["tool_id"] == "contract-check"


@pytest.mark.asyncio
async def test_debug_tool_returns_200(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    await api.seed_tool(tenant, tool_id="my-tool")

    resp = await api.client.post(
        "/api/tools/my-tool/debug",
        headers=api.auth(user, tenant),
        json={"params": {"contractText": "付款条件"}},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["statusCode"] == 200
    assert body["responseBody"] == {"ok": True}


@pytest.mark.asyncio
async def test_debug_disabled_tool_returns_409(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    await api.seed_tool(tenant, tool_id="my-tool", permission_mode="disabled")

    resp = await api.client.post(
        "/api/tools/my-tool/debug",
        headers=api.auth(user, tenant),
        json={"params": {}},
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TOOL_DISABLED"


@pytest.mark.asyncio
async def test_create_tool_rejects_ssrf_endpoint(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    resp = await api.client.post(
        "/api/tools",
        headers=api.auth(user, tenant),
        json={
            "name": "X",
            "tool_id": "x",
            "type": "http",
            "endpoint": "http://169.254.169.254/latest",
            "risk_level": "low",
        },
    )
    assert resp.status_code == 422


# ── dashboard / models / health ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_dashboard_returns_metrics(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    resp = await api.client.get("/api/dashboard/admin", headers=api.auth(user, tenant))

    assert resp.status_code == 200
    metrics = resp.json()["data"]["metrics"]
    assert set(metrics) == {"totalAgents", "todayCalls", "avgLatencyMs", "errorRate"}


@pytest.mark.asyncio
async def test_dashboard_cross_tenant_no_leak(api) -> None:
    tenant_a, user_a = await api.seed(role="agent_admin")
    tenant_b, user_b = await api.seed(role="agent_admin")
    await api.seed_agent(tenant_a, agent_id="a-bot")

    resp_a = await api.client.get("/api/dashboard/admin", headers=api.auth(user_a, tenant_a))
    resp_b = await api.client.get("/api/dashboard/admin", headers=api.auth(user_b, tenant_b))

    assert resp_a.json()["data"]["metrics"]["totalAgents"] == 1
    assert resp_b.json()["data"]["metrics"]["totalAgents"] == 0


@pytest.mark.asyncio
async def test_models_list_returns_providers(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    resp = await api.client.get("/api/models", headers=api.auth(user, tenant))

    assert resp.status_code == 200
    providers = resp.json()["data"]
    assert providers[0]["provider"] == "openai"
    assert providers[0]["modelCount"] == 1


@pytest.mark.asyncio
async def test_health_ready_reports_dependency_status(api) -> None:
    # Real Postgres/Redis are up (testcontainers); Dify is not, so the probe
    # reports degraded with per-dependency granularity.
    resp = await api.client.get("/api/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"
    assert body["checks"]["dify"] == "error"
