"""Acceptance case for tool registration, permission gates, disabled and
unconfigured targets, auth injection, retry/circuit semantics and masking.

The case drives the public ``/api/tools`` surface plus the approved-task
execution path the platform uses to actually run a tool, against a real
PostgreSQL and Redis (testcontainers). The only mocked boundary is the upstream
HTTP endpoint, recorded through an ``httpx.MockTransport`` so every clause
observes what the platform sent, how many times, and what it returned.
"""

import base64
import importlib
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.models.task import Task
from app.models.tool import ToolDebugCase, ToolRegistry
from app.services.tool_proxy import ToolProxy

TC_ID = "TC-tech-tools-01"

_PROCESS_TASK_MODULE = importlib.import_module("app.workers.process_task")
_TOOL_PROXY_MODULE = importlib.import_module("app.services.tool_proxy")

_ENDPOINT = "http://upstream.example/tool"
_Handler = Callable[[httpx.Request, int], httpx.Response]


# ── helpers ─────────────────────────────────────────────────────────────────


class _ManualClock:
    """Stand-in for the ``time`` module exposing a controllable ``monotonic``.

    ``ToolProxy`` reads ``time.monotonic`` for the circuit-breaker window and for
    latency, so replacing the module object *in the proxy's own namespace* moves
    the breaker clock without freezing the process-wide clock (which would also
    stop the event loop's own timekeeping).
    """

    def __init__(self, now: float) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


def _create_payload(tool_id: str = "erp-lookup", **overrides: object) -> dict[str, object]:
    """A minimal valid create body; ``overrides`` replaces individual fields."""
    body: dict[str, object] = {"name": "ERP Lookup", "tool_id": tool_id, "endpoint": _ENDPOINT}
    body.update(overrides)
    return body


def _install_proxy(api: Any, handler: _Handler) -> list[httpx.Request]:
    """Point the app's ToolProxy at a recording transport; return the call log."""
    calls: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request, len(calls))

    api.app.state.tool_proxy = ToolProxy(transport=httpx.MockTransport(_record))
    return calls


def _ok(_request: httpx.Request, _attempt: int) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def _echo_received_headers(request: httpx.Request, _attempt: int) -> httpx.Response:
    """Upstream stub that reports the request headers it received.

    Echoing makes the outbound request observable from the response side: the
    test can read what the platform actually sent instead of comparing against a
    constant body that could never contain a leaked credential.
    """
    return httpx.Response(
        200,
        json={"receivedHeaders": {name.lower(): value for name, value in request.headers.items()}},
    )


def _always(status_code: int) -> _Handler:
    def _handler(_request: httpx.Request, _attempt: int) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "down"})

    return _handler


async def _insert_tool(
    api: Any, *, tool_id: str, tenant_id: uuid.UUID | None, **overrides: Any
) -> None:
    """Insert a registry row the create API cannot express (global, archived, …)."""
    fields: dict[str, Any] = {
        "name": tool_id,
        "type": "http",
        "endpoint": _ENDPOINT,
        "method": "POST",
        "risk_level": "medium",
        "permission_mode": "auto",
        "auth_type": "none",
        "timeout_ms": 10_000,
        "retry_policy": {"max_retries": 2, "base_delay_ms": 1000},
        "status": "active",
    }
    fields.update(overrides)
    async with api.db.session() as session:
        session.add(ToolRegistry(tool_id=tool_id, tenant_id=tenant_id, **fields))
        await session.commit()


async def _seed_approved_task(api: Any, tenant: Any, user: Any, tool_id: str) -> Task:
    """Persist an approved tool task whose payload names ``tool_id``."""
    task = Task(
        tenant_id=tenant.id,
        creator_id=user.id,
        type="tool_approval",
        title="Approve tool",
        priority="high",
        status="approved",
        payload={"tool_id": tool_id, "params": {"q": "headcount"}},
    )
    async with api.db.session() as session:
        session.add(task)
        await session.commit()
    return task


async def _run_execution(
    api: Any, monkeypatch: pytest.MonkeyPatch, task: Task, proxy: ToolProxy
) -> None:
    """Drive the platform's approved-task execution of ``task`` end to end."""
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", api.db.session)
    await _PROCESS_TASK_MODULE._process(
        str(task.id),
        str(uuid.uuid4()),
        task.payload,
        tool_proxy=proxy,
        claim_execution=lambda _outbox_id: True,
    )


async def _task_detail(api: Any, headers: dict[str, str], task_id: str) -> dict[str, Any]:
    resp = await api.client.get(f"/api/tasks/{task_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _open_circuit(
    api: Any, monkeypatch: pytest.MonkeyPatch, tenant: Any, user: Any, proxy: ToolProxy
) -> None:
    """Drive five consecutive failing executions so the breaker opens."""
    for _ in range(5):
        task = await _seed_approved_task(api, tenant, user, "flaky")
        await _run_execution(api, monkeypatch, task, proxy)


# ── registration ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_the_registered_tool_and_detail_echoes_it(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)

    resp = await api.client.post("/api/tools", headers=headers, json=_create_payload())

    assert resp.status_code == 201, resp.text
    body = resp.json()["data"]
    assert body["tool_id"] == "erp-lookup"
    assert body["tenant_id"] == str(tenant.id)
    assert body["created_by"] == str(admin.id)
    assert body["endpoint"] == _ENDPOINT
    # C-68 fixes the retry default at 2 when the tool configures no policy.
    assert body["retry_policy"]["max_retries"] == 2

    detail = await api.client.get("/api/tools/erp-lookup", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"] == body


@pytest.mark.asyncio
async def test_create_echoes_every_supplied_field_and_detail_matches_creation(api) -> None:
    """Every configured registration field survives create → detail unchanged.

    C-64~C-70 pin only the retry default, so the remaining registration fields
    are covered by round-tripping explicit values instead of restating the
    implementation's defaults as if the contract fixed them.
    """
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    supplied: dict[str, Any] = {
        "description": "employee directory",
        "method": "PUT",
        "risk_level": "high",
        "permission_mode": "confirm",
        "timeout_ms": 2500,
        "retry_policy": {"max_retries": 1, "base_delay_ms": 250},
        "circuit_breaker": {"threshold": 3, "recovery_seconds": 30},
        "auth_type": "api_key",
        "auth_config": {"api_key": "key-1"},
    }

    resp = await api.client.post(
        "/api/tools", headers=headers, json=_create_payload("erp-write", **supplied)
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()["data"]
    for field, value in supplied.items():
        assert body[field] == value, field

    detail = await api.client.get("/api/tools/erp-write", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"] == body


@pytest.mark.asyncio
async def test_create_with_existing_identifier_returns_409_including_other_tenants(api) -> None:
    tenant_a, admin_a = await api.seed(role="agent_admin")
    tenant_b, admin_b = await api.seed(role="agent_admin")

    first = await api.client.post(
        "/api/tools", headers=api.auth(admin_a, tenant_a), json=_create_payload()
    )
    assert first.status_code == 201

    same_tenant = await api.client.post(
        "/api/tools", headers=api.auth(admin_a, tenant_a), json=_create_payload()
    )
    assert same_tenant.status_code == 409
    assert same_tenant.json()["error"]["code"] == "DUPLICATE_TOOL_ID"

    other_tenant = await api.client.post(
        "/api/tools", headers=api.auth(admin_b, tenant_b), json=_create_payload()
    )
    assert other_tenant.status_code == 409
    assert other_tenant.json()["error"]["code"] == "DUPLICATE_TOOL_ID"


@pytest.mark.asyncio
async def test_create_with_non_lowercase_hyphenated_identifier_returns_422(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)

    for tool_id in ("", "Erp", "ERP-Lookup", "erp_lookup", "-erp", "erp lookup", "erp.lookup"):
        resp = await api.client.post("/api/tools", headers=headers, json=_create_payload(tool_id))
        assert resp.status_code == 422, (tool_id, resp.status_code)
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_create_with_unusable_endpoint_returns_422(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)

    endpoints = (
        "ftp://erp.example/api",
        "erp.example/api",
        "http://",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/computeMetadata/v1/",
    )
    for endpoint in endpoints:
        resp = await api.client.post(
            "/api/tools", headers=headers, json=_create_payload(endpoint=endpoint)
        )
        assert resp.status_code == 422, (endpoint, resp.status_code)
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ── permission gates ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_global_tool_write_is_platform_admin_only(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    platform_tenant, platform_admin = await api.seed(role="platform_admin")
    await _insert_tool(api, tool_id="platform-echo", tenant_id=None)

    tenant_headers = api.auth(admin, tenant)
    denied_update = await api.client.patch(
        "/api/tools/platform-echo", headers=tenant_headers, json={"name": "hijacked"}
    )
    assert denied_update.status_code == 403
    assert denied_update.json()["error"]["code"] == "FORBIDDEN"

    denied_delete = await api.client.delete("/api/tools/platform-echo", headers=tenant_headers)
    assert denied_delete.status_code == 403
    assert denied_delete.json()["error"]["code"] == "FORBIDDEN"

    # The refused writes left the shared row untouched.
    unchanged = await api.client.get("/api/tools/platform-echo", headers=tenant_headers)
    assert unchanged.json()["data"]["name"] == "platform-echo"

    allowed = await api.client.patch(
        "/api/tools/platform-echo",
        headers=api.auth(platform_admin, platform_tenant),
        json={"name": "Platform Echo"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["name"] == "Platform Echo"


@pytest.mark.asyncio
async def test_reads_are_open_to_any_authenticated_user_but_writes_are_admin_only(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    employee = await api.seed_user(tenant, role="employee")
    await api.client.post("/api/tools", headers=api.auth(admin, tenant), json=_create_payload())

    employee_headers = api.auth(employee, tenant)
    assert (await api.client.get("/api/tools", headers=employee_headers)).status_code == 200
    assert (
        await api.client.get("/api/tools/erp-lookup", headers=employee_headers)
    ).status_code == 200

    denied_create = await api.client.post(
        "/api/tools", headers=employee_headers, json=_create_payload("employee-tool")
    )
    assert denied_create.status_code == 403
    assert denied_create.json()["error"]["code"] == "FORBIDDEN"

    denied_update = await api.client.patch(
        "/api/tools/erp-lookup", headers=employee_headers, json={"name": "renamed"}
    )
    assert denied_update.status_code == 403
    assert denied_update.json()["error"]["code"] == "FORBIDDEN"

    denied_debug = await api.client.post(
        "/api/tools/erp-lookup/debug", headers=employee_headers, json={"params": {}}
    )
    assert denied_debug.status_code == 403
    assert denied_debug.json()["error"]["code"] == "FORBIDDEN"

    assert (await api.client.get("/api/tools")).status_code == 401


# ── visibility ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_is_tenant_filtered_includes_global_tools_and_detail_is_404_across_tenants(
    api,
) -> None:
    tenant_a, admin_a = await api.seed(role="agent_admin")
    tenant_b, admin_b = await api.seed(role="agent_admin")
    headers_a = api.auth(admin_a, tenant_a)

    assert (
        await api.client.post(
            "/api/tools", headers=headers_a, json=_create_payload("tenant-a-tool")
        )
    ).status_code == 201
    assert (
        await api.client.post(
            "/api/tools", headers=api.auth(admin_b, tenant_b), json=_create_payload("tenant-b-tool")
        )
    ).status_code == 201
    await _insert_tool(api, tool_id="global-tool", tenant_id=None)

    listed = await api.client.get("/api/tools", headers=headers_a)
    assert listed.status_code == 200
    payload = listed.json()["data"]
    assert isinstance(payload, list)
    assert {tool["tool_id"] for tool in payload} == {"tenant-a-tool", "global-tool"}

    cross_tenant = await api.client.get("/api/tools/tenant-b-tool", headers=headers_a)
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["error"]["code"] == "TOOL_NOT_FOUND"


@pytest.mark.xfail(reason="C-65 archived-tool list filter not implemented at main@68ad4b5")
@pytest.mark.asyncio
async def test_list_excludes_archived_tools(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await _insert_tool(api, tool_id="retired-tool", tenant_id=tenant.id, status="archived")

    listed = await api.client.get("/api/tools", headers=api.auth(admin, tenant))

    assert listed.status_code == 200
    assert [tool["tool_id"] for tool in listed.json()["data"]] == []


# ── disabled / unconfigured targets ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_debug_of_disabled_tool_returns_409_without_calling_upstream(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    await _insert_tool(api, tool_id="switched-off", tenant_id=tenant.id, permission_mode="disabled")
    calls = _install_proxy(api, _ok)

    resp = await api.client.post(
        "/api/tools/switched-off/debug", headers=headers, json={"params": {"q": 1}}
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TOOL_DISABLED"
    assert calls == []


@pytest.mark.asyncio
async def test_debug_of_tool_without_endpoint_returns_422_without_calling_upstream(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    await _insert_tool(api, tool_id="unconfigured", tenant_id=tenant.id, endpoint=None)
    calls = _install_proxy(api, _ok)

    resp = await api.client.post(
        "/api/tools/unconfigured/debug", headers=headers, json={"params": {"q": 1}}
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "TOOL_NOT_CONFIGURED"
    assert calls == []


@pytest.mark.xfail(
    reason="C-67 disabled-mode gate not applied on approval-triggered execution at main@68ad4b5"
)
@pytest.mark.asyncio
async def test_execution_of_disabled_tool_never_reaches_upstream(api, monkeypatch) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await _insert_tool(api, tool_id="switched-off", tenant_id=tenant.id, permission_mode="disabled")
    calls = _install_proxy(api, _ok)
    task = await _seed_approved_task(api, tenant, admin, "switched-off")

    await _run_execution(api, monkeypatch, task, api.app.state.tool_proxy)

    assert calls == []
    assert (await _task_detail(api, api.auth(admin, tenant), str(task.id)))["status"] == "failed"


@pytest.mark.asyncio
async def test_execution_of_tool_without_endpoint_never_reaches_upstream(api, monkeypatch) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    await _insert_tool(api, tool_id="unconfigured", tenant_id=tenant.id, endpoint=None)
    calls = _install_proxy(api, _ok)
    task = await _seed_approved_task(api, tenant, admin, "unconfigured")

    await _run_execution(api, monkeypatch, task, api.app.state.tool_proxy)

    assert calls == []
    detail = await _task_detail(api, headers, str(task.id))
    assert detail["status"] == "failed"
    # C-67: execution and debug apply the same judgement. The debug surface
    # exposes the stable code for a missing endpoint, and the execution row
    # reports that same failure instead of an upstream one.
    debug = await api.client.post(
        "/api/tools/unconfigured/debug", headers=headers, json={"params": {}}
    )
    assert debug.status_code == 422
    assert debug.json()["error"]["code"] == "TOOL_NOT_CONFIGURED"
    assert detail["error_message"] == debug.json()["error"]["message"]


# ── auth injection ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_injection_only_reaches_the_outbound_request_by_auth_type(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    calls = _install_proxy(api, _echo_received_headers)

    basic = "Basic " + base64.b64encode(b"svc-user:basic-secret").decode()
    # (tool_id, auth_type, auth_config, expected headers, configured secret)
    cases: list[tuple[str, str, dict[str, Any] | None, dict[str, str], str | None]] = [
        ("auth-none", "none", None, {}, None),
        (
            "auth-bearer",
            "bearer",
            {"token": "tok-bearer"},
            {"authorization": "Bearer tok-bearer"},
            "tok-bearer",
        ),
        (
            "auth-oauth",
            "oauth2",
            {"access_token": "tok-oauth"},
            {"authorization": "Bearer tok-oauth"},
            "tok-oauth",
        ),
        ("auth-apikey", "api_key", {"api_key": "key-1"}, {"x-api-key": "key-1"}, "key-1"),
        (
            "auth-custom",
            "api_key",
            {"api_key": "key-2", "header_name": "X-Tenant-Key"},
            {"x-tenant-key": "key-2"},
            "key-2",
        ),
        (
            "auth-basic",
            "basic",
            {"username": "svc-user", "password": "basic-secret"},
            {"authorization": basic},
            "basic-secret",
        ),
    ]
    credential_headers = ("authorization", "x-api-key", "x-tenant-key")
    params = {"query": "headcount"}

    for tool_id, auth_type, auth_config, expected, secret in cases:
        created = await api.client.post(
            "/api/tools",
            headers=headers,
            json=_create_payload(tool_id, auth_type=auth_type, auth_config=auth_config),
        )
        assert created.status_code == 201, created.text

        del calls[:]
        resp = await api.client.post(
            f"/api/tools/{tool_id}/debug",
            headers=headers,
            json={"params": params, "save_as_test_case": True, "name": f"{tool_id}-case"},
        )
        assert resp.status_code == 200, resp.text

        sent = {name.lower(): value for name, value in calls[0].headers.items()}
        for name, value in expected.items():
            assert sent.get(name) == value, (tool_id, name)
        for name in credential_headers:
            if name not in expected:
                assert name not in sent, (tool_id, name)

        # C-66: the upstream reports what it received — the credential travels
        # as an outbound header, and nowhere else the platform controls.
        body = resp.json()["data"]
        echoed = body["responseBody"]["receivedHeaders"]
        for name, value in expected.items():
            assert echoed[name] == value, (tool_id, name)

        async with api.db.session() as session:
            case = await session.get(ToolDebugCase, uuid.UUID(body["savedCaseId"]))
            assert case is not None
            # The saved copy of the request holds the caller's params, never the
            # configured credential.
            assert json.loads(case.request_body or "{}") == params, tool_id
            if secret is not None:
                assert secret not in (case.request_body or ""), tool_id

        platform_fields = {key: value for key, value in body.items() if key != "responseBody"}
        if secret is not None:
            assert secret not in json.dumps(platform_fields), tool_id


@pytest.mark.asyncio
async def test_auth_injection_omits_the_header_when_the_credential_is_missing(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    calls = _install_proxy(api, _echo_received_headers)

    cases = (
        ("auth-bearer-empty", "bearer", {}),
        ("auth-oauth-empty", "oauth2", {}),
        ("auth-apikey-empty", "api_key", {}),
    )
    for tool_id, auth_type, auth_config in cases:
        created = await api.client.post(
            "/api/tools",
            headers=headers,
            json=_create_payload(tool_id, auth_type=auth_type, auth_config=auth_config),
        )
        assert created.status_code == 201, created.text

        del calls[:]
        resp = await api.client.post(
            f"/api/tools/{tool_id}/debug", headers=headers, json={"params": {}}
        )
        assert resp.status_code == 200, resp.text

        sent = {key.lower() for key in calls[0].headers}
        assert "authorization" not in sent, tool_id
        assert "x-api-key" not in sent, tool_id
        # The upstream received no credential either.
        echoed = resp.json()["data"]["responseBody"]["receivedHeaders"]
        assert "authorization" not in echoed, tool_id
        assert "x-api-key" not in echoed, tool_id


@pytest.mark.xfail(
    reason=(
        "C-66 credentials-absent-no-header (basic) not implemented at "
        "main@68ad4b5: empty credentials are encoded into the header"
    ),
    strict=False,
)
@pytest.mark.asyncio
async def test_auth_injection_omits_the_basic_header_when_credentials_are_missing(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    calls = _install_proxy(api, _echo_received_headers)

    for tool_id, auth_config in (("auth-basic-empty", {}), ("auth-basic-null", None)):
        created = await api.client.post(
            "/api/tools",
            headers=headers,
            json=_create_payload(tool_id, auth_type="basic", auth_config=auth_config),
        )
        assert created.status_code == 201, created.text

        del calls[:]
        resp = await api.client.post(
            f"/api/tools/{tool_id}/debug", headers=headers, json={"params": {}}
        )
        assert resp.status_code == 200, resp.text

        # C-66: with no credentials configured, Basic injects no header at all.
        sent = {key.lower() for key in calls[0].headers}
        assert "authorization" not in sent, tool_id
        echoed = resp.json()["data"]["responseBody"]["receivedHeaders"]
        assert "authorization" not in echoed, tool_id


# ── method / masking ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_parameters_use_the_query_string_and_other_methods_a_json_body(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    calls = _install_proxy(api, _ok)
    await _insert_tool(api, tool_id="search", tenant_id=tenant.id, method="GET")
    await _insert_tool(api, tool_id="writer", tenant_id=tenant.id, method="PUT")

    params = {"q": "headcount", "page": "2"}
    get_resp = await api.client.post(
        "/api/tools/search/debug", headers=headers, json={"params": params}
    )
    assert get_resp.status_code == 200, get_resp.text
    assert calls[0].method == "GET"
    assert calls[0].url.path == "/tool"
    assert dict(calls[0].url.params) == params
    assert calls[0].content == b""

    put_resp = await api.client.post(
        "/api/tools/writer/debug", headers=headers, json={"params": params}
    )
    assert put_resp.status_code == 200, put_resp.text
    assert calls[1].method == "PUT"
    assert calls[1].url.query == b""
    assert json.loads(calls[1].content.decode()) == params


@pytest.mark.asyncio
async def test_debug_masks_the_response_but_sends_the_request_body_verbatim(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    await _insert_tool(api, tool_id="crm", tenant_id=tenant.id)
    calls = _install_proxy(
        api,
        lambda _request, _attempt: httpx.Response(
            200, json={"phone": "13800138000", "note": "call 13900139000"}
        ),
    )

    params = {"phone": "13800138000", "note": "call 13900139000"}
    resp = await api.client.post("/api/tools/crm/debug", headers=headers, json={"params": params})

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["statusCode"] == 200
    assert body["responseBody"] == {"phone": "***", "note": "call ***"}
    assert body["savedCaseId"] is None
    # The upstream receives the real values: requests are never masked.
    assert json.loads(calls[0].content.decode()) == params


@pytest.mark.asyncio
async def test_saved_debug_case_masks_both_request_and_response(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    await _insert_tool(api, tool_id="crm", tenant_id=tenant.id)
    _install_proxy(
        api,
        lambda _request, _attempt: httpx.Response(200, json={"phone": "13800138000", "ok": True}),
    )

    resp = await api.client.post(
        "/api/tools/crm/debug",
        headers=headers,
        json={"params": {"phone": "13800138000"}, "save_as_test_case": True, "name": "case-1"},
    )

    assert resp.status_code == 200, resp.text
    saved_case_id = resp.json()["data"]["savedCaseId"]
    assert saved_case_id

    async with api.db.session() as session:
        case = await session.get(ToolDebugCase, uuid.UUID(saved_case_id))
        assert case is not None
        assert case.name == "case-1"
        assert case.status_code == 200
        assert json.loads(case.request_body or "{}") == {"phone": "***"}
        assert json.loads(case.response_body or "{}") == {"phone": "***", "ok": True}


# ── retry, failure and circuit semantics ────────────────────────────────────


@pytest.mark.asyncio
async def test_debug_never_retries_and_reports_the_upstream_status(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    await _insert_tool(
        api,
        tool_id="degraded",
        tenant_id=tenant.id,
        retry_policy={"max_retries": 2, "base_delay_ms": 0},
    )
    calls = _install_proxy(api, _always(503))

    resp = await api.client.post("/api/tools/degraded/debug", headers=headers, json={"params": {}})

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["statusCode"] == 503
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_execution_retries_per_policy_and_fails_when_retries_are_exhausted(
    api, monkeypatch
) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await _insert_tool(
        api,
        tool_id="slow-erp",
        tenant_id=tenant.id,
        retry_policy={"max_retries": 2, "base_delay_ms": 50},
    )
    calls = _install_proxy(api, _always(503))
    task = await _seed_approved_task(api, tenant, admin, "slow-erp")

    started = time.monotonic()
    await _run_execution(api, monkeypatch, task, api.app.state.tool_proxy)
    elapsed = time.monotonic() - started

    # One initial call plus ``max_retries`` retries on a retryable upstream status.
    assert len(calls) == 3
    # C-68 backoff is ``base * 2**attempt``: with a 50 ms base the two waits sum
    # to at least 50 + 100 ms, which a constant or absent delay cannot satisfy.
    assert elapsed >= 0.15

    detail = await _task_detail(api, api.auth(admin, tenant), str(task.id))
    assert detail["status"] == "failed"
    # C-68 exhaustion is the upstream failure (502 TOOL_UPSTREAM_ERROR): the row
    # names the upstream status the retries kept hitting, not a breaker or
    # configuration refusal.
    assert "503" in detail["error_message"]
    # Tool retry counting is a different domain from the task retry budget.
    assert detail["retry_count"] == 0


@pytest.mark.asyncio
async def test_execution_does_not_retry_a_non_retryable_upstream_status(api, monkeypatch) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await _insert_tool(
        api,
        tool_id="strict-erp",
        tenant_id=tenant.id,
        retry_policy={"max_retries": 2, "base_delay_ms": 0},
    )
    calls = _install_proxy(api, _always(400))
    task = await _seed_approved_task(api, tenant, admin, "strict-erp")

    await _run_execution(api, monkeypatch, task, api.app.state.tool_proxy)

    assert len(calls) == 1
    detail = await _task_detail(api, api.auth(admin, tenant), str(task.id))
    assert detail["status"] == "failed"
    # C-68: a non-retryable upstream status is still reported as the upstream
    # failure, naming the status the caller never retried.
    assert "400" in detail["error_message"]


@pytest.mark.asyncio
async def test_circuit_opens_after_five_consecutive_failures_and_blocks_later_calls(
    api, monkeypatch
) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await _insert_tool(
        api,
        tool_id="flaky",
        tenant_id=tenant.id,
        retry_policy={"max_retries": 0, "base_delay_ms": 0},
    )
    calls = _install_proxy(api, _always(503))
    proxy = api.app.state.tool_proxy

    await _open_circuit(api, monkeypatch, tenant, admin, proxy)
    assert len(calls) == 5

    blocked = await _seed_approved_task(api, tenant, admin, "flaky")
    await _run_execution(api, monkeypatch, blocked, proxy)

    # The open breaker rejects before any request leaves the platform.
    assert len(calls) == 5
    assert (await _task_detail(api, api.auth(admin, tenant), str(blocked.id)))["status"] == "failed"


@pytest.mark.xfail(
    reason=(
        "C-68 debug-shares-the-breaker (调试同左) not implemented at "
        "main@68ad4b5: the debug path never consults the open circuit"
    ),
    strict=False,
)
@pytest.mark.asyncio
async def test_debug_of_a_tool_with_an_open_circuit_returns_503_tool_circuit_open(
    api, monkeypatch
) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    await _insert_tool(
        api,
        tool_id="flaky",
        tenant_id=tenant.id,
        retry_policy={"max_retries": 0, "base_delay_ms": 0},
    )
    calls = _install_proxy(api, _always(503))
    proxy = api.app.state.tool_proxy

    await _open_circuit(api, monkeypatch, tenant, admin, proxy)
    assert len(calls) == 5

    # C-68: while the breaker is open every surface is refused with 503
    # TOOL_CIRCUIT_OPEN without touching the upstream — debug included.
    resp = await api.client.post("/api/tools/flaky/debug", headers=headers, json={"params": {}})

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "TOOL_CIRCUIT_OPEN"
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_circuit_admits_exactly_one_probe_after_the_recovery_window(api, monkeypatch) -> None:
    clock = _ManualClock(1000.0)
    monkeypatch.setattr(_TOOL_PROXY_MODULE, "time", clock)
    tenant, admin = await api.seed(role="agent_admin")
    await _insert_tool(
        api,
        tool_id="flaky",
        tenant_id=tenant.id,
        retry_policy={"max_retries": 0, "base_delay_ms": 0},
    )
    calls = _install_proxy(api, _always(503))
    proxy = api.app.state.tool_proxy

    await _open_circuit(api, monkeypatch, tenant, admin, proxy)
    assert len(calls) == 5

    # Inside the window every call is still rejected without touching upstream.
    clock.now += 30.0
    blocked = await _seed_approved_task(api, tenant, admin, "flaky")
    await _run_execution(api, monkeypatch, blocked, proxy)
    assert len(calls) == 5

    # After the window exactly one trial request is admitted…
    clock.now += 31.0
    probe = await _seed_approved_task(api, tenant, admin, "flaky")
    await _run_execution(api, monkeypatch, probe, proxy)
    assert len(calls) == 6

    # …and the rest are still rejected while that trial is the only one allowed.
    others = await _seed_approved_task(api, tenant, admin, "flaky")
    await _run_execution(api, monkeypatch, others, proxy)
    assert len(calls) == 6


@pytest.mark.asyncio
async def test_debug_upstream_transport_failure_returns_502_tool_upstream_error(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    await _insert_tool(api, tool_id="unreachable", tenant_id=tenant.id)

    def _boom(request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install_proxy(api, _boom)

    resp = await api.client.post(
        "/api/tools/unreachable/debug", headers=headers, json={"params": {}}
    )

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "TOOL_UPSTREAM_ERROR"
