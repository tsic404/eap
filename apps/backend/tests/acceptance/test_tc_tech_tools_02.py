"""Acceptance case for the task state machine, concurrency, expiry, retry
budget and at-least-once dispatch (design AC-T08).

The case drives the public ``/api/tasks`` and ``/api/conversations`` surfaces
plus the platform's own dispatch/execution workers against a real PostgreSQL
and Redis (testcontainers). The only mocked boundaries are the Dify chat stream
and the upstream tool endpoint, both recorded so every clause observes what the
platform actually did.
"""

import asyncio
import importlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from rq import Queue
from sqlalchemy import select

from app.models.task import Task
from app.models.task_outbox import TaskOutboxEvent
from app.models.tool import ToolRegistry
from app.services.conversation import ConversationService
from app.services.task_expiry import SCAN_INTERVAL_SECONDS, expire_overdue_approvals
from app.services.task_reconcile import reconcile_undelivered
from app.services.tool_proxy import ToolProxy

TC_ID = "TC-tech-tools-02"

_PROCESS_TASK_MODULE = importlib.import_module("app.workers.process_task")
_CONVERSATION_MODULE = importlib.import_module("app.services.conversation")

_ENDPOINT = "http://upstream.example/tool"
_TOOL_APPROVAL_QUEUE = "tool-approval"


# ── helpers ─────────────────────────────────────────────────────────────────


def _install_proxy(api: Any) -> list[httpx.Request]:
    """Point the app's ToolProxy at a recording transport; return the call log."""
    calls: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    api.app.state.tool_proxy = ToolProxy(transport=httpx.MockTransport(_record))
    return calls


async def _insert_tool(api: Any, *, tool_id: str, tenant_id: uuid.UUID, **overrides: Any) -> None:
    """Insert a registry row with the fields a clause needs."""
    fields: dict[str, Any] = {
        "name": tool_id,
        "type": "http",
        "endpoint": _ENDPOINT,
        "method": "POST",
        "risk_level": "medium",
        "permission_mode": "auto",
        "auth_type": "none",
        "timeout_ms": 10_000,
        "retry_policy": {"max_retries": 2, "base_delay_ms": 0},
        "status": "active",
    }
    fields.update(overrides)
    async with api.db.session() as session:
        session.add(ToolRegistry(tool_id=tool_id, tenant_id=tenant_id, **fields))
        await session.commit()


async def _seed_task(
    api: Any,
    tenant: Any,
    user: Any,
    *,
    status: str = "pending",
    payload: dict[str, Any] | None = None,
    creator_id: uuid.UUID | None = None,
    retry_count: int = 0,
    max_retries: int = 3,
    expires_at: datetime | None = None,
) -> Task:
    """Persist a tool-approval task in ``status`` for ``tenant``."""
    task = Task(
        tenant_id=tenant.id,
        creator_id=creator_id or user.id,
        type="tool_approval",
        title="Approve tool",
        priority="high",
        status=status,
        payload=payload or {"tool_id": "erp", "params": {}},
        retry_count=retry_count,
        max_retries=max_retries,
        expires_at=expires_at,
    )
    async with api.db.session() as session:
        session.add(task)
        await session.commit()
    return task


async def _task_detail(api: Any, headers: dict[str, str], task_id: str) -> dict[str, Any]:
    resp = await api.client.get(f"/api/tasks/{task_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _post(api: Any, headers: dict[str, str], task_id: str, action: str, body: Any) -> Any:
    return await api.client.post(f"/api/tasks/{task_id}/{action}", headers=headers, json=body)


async def _outbox_rows(api: Any, task_id: str) -> list[TaskOutboxEvent]:
    async with api.db.session() as session:
        result = await session.execute(
            select(TaskOutboxEvent).where(TaskOutboxEvent.task_id == task_id)
        )
        return list(result.scalars().all())


class _FakeDifyClient:
    """Minimal Dify client yielding a fixed SSE frame sequence (no live Dify)."""

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = frames

    async def get(self, path: str, query: Any = None, *, retries: int = 3, timeout: Any = None):
        return {"data": [], "has_more": False}

    async def post_stream(self, path: str, body: Any) -> AsyncIterator[bytes]:
        for frame in self._frames:
            yield frame

    async def aclose(self) -> None:
        pass


def _frame(event: str, data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps({'event': event, **data})}\n\n".encode()


def _sse_data(body: str, event: str) -> dict[str, Any]:
    """Return the JSON payload of one named SSE event in a multi-frame body."""
    for block in body.split("\n\n"):
        if block.startswith(f"event: {event}\n"):
            return json.loads(block.split("data: ", 1)[1])
    raise AssertionError(f"SSE event {event!r} not found in stream body")


# ── state machine ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_transitions_to_approved_and_dispatches_the_task(api, wait_for_job) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    task = await _seed_task(api, tenant, admin)

    resp = await _post(api, headers, str(task.id), "approve", {})

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "approved"

    # The dispatch record is written with the transition and the job is enqueued.
    rows = await _outbox_rows(api, str(task.id))
    assert len(rows) == 1
    assert rows[0].type == _TOOL_APPROVAL_QUEUE
    assert rows[0].status == "approved"
    assert rows[0].delivered_at is not None

    assert [entry["task_id"] for entry in api.enqueued] == [str(task.id)]
    assert api.enqueued[0]["queue_name"] == _TOOL_APPROVAL_QUEUE
    queue = Queue(_TOOL_APPROVAL_QUEUE, connection=api.redis_conn)
    job = await wait_for_job(queue, api.enqueued[0]["outbox_id"])
    assert job is not None
    assert job.args[0] == str(task.id)


@pytest.mark.asyncio
async def test_cancel_succeeds_from_pending_and_approved_without_dispatching(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    pending = await _seed_task(api, tenant, admin)
    approved = await _seed_task(api, tenant, admin, status="approved")

    for task in (pending, approved):
        resp = await _post(api, headers, str(task.id), "cancel", None)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "cancelled"

    # Cancelling is terminal and carries no queue work from either edge.
    assert api.enqueued == []
    assert await _outbox_rows(api, str(pending.id)) == []
    assert await _outbox_rows(api, str(approved.id)) == []


@pytest.mark.asyncio
async def test_invalid_transition_returns_422_naming_both_statuses(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    task = await _seed_task(api, tenant, admin, status="completed")

    resp = await _post(api, headers, str(task.id), "cancel", None)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TRANSITION"
    message = resp.json()["error"]["message"]
    assert "completed" in message
    assert "cancelled" in message


@pytest.mark.asyncio
async def test_approving_an_expired_task_returns_422_task_expired(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    task = await _seed_task(api, tenant, admin, expires_at=datetime.now(UTC) - timedelta(minutes=1))

    resp = await _post(api, headers, str(task.id), "approve", {})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "TASK_EXPIRED"


@pytest.mark.asyncio
async def test_approving_a_terminal_task_returns_422_invalid_transition(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    task = await _seed_task(api, tenant, admin, status="cancelled")

    resp = await _post(api, headers, str(task.id), "approve", {})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TRANSITION"


@pytest.mark.asyncio
async def test_timeout_scan_cancels_an_expired_pending_approval(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    task = await _seed_task(api, tenant, admin, expires_at=datetime.now(UTC) - timedelta(hours=1))

    # The lifespan — and with it the 15s scheduler — does not run under an ASGI
    # transport, so drive the same scan the scheduled job runs and poll the task
    # the way a client would, for one full design cycle.
    deadline = time.monotonic() + SCAN_INTERVAL_SECONDS
    status = "pending"
    detail: dict[str, Any] = {}
    while time.monotonic() < deadline:
        await expire_overdue_approvals(api.db.session)
        detail = await _task_detail(api, headers, str(task.id))
        status = detail["status"]
        if status == "cancelled":
            break
        await asyncio.sleep(0.5)

    assert status == "cancelled"
    # C-75: the user-visible result of a timeout cancellation.
    assert detail["result"] == {"reason": "超时取消"}
    resp = await _post(api, headers, str(task.id), "approve", {})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TRANSITION"


@pytest.mark.asyncio
async def test_concurrent_transition_of_the_same_task_yields_exactly_one_winner(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    task = await _seed_task(api, tenant, admin)

    responses = await asyncio.gather(
        _post(api, headers, str(task.id), "approve", {}),
        _post(api, headers, str(task.id), "approve", {}),
    )

    # C-73: exactly one request wins the transition. The loser either lost the
    # conditional update (409 TASK_ALREADY_PROCESSED) or read the row after the
    # winner committed and named the illegal approved -> approved transition
    # (422 INVALID_TRANSITION); both are contract outcomes, so the status stays
    # open while the code is pinned to it.
    expected_codes = {409: "TASK_ALREADY_PROCESSED", 422: "INVALID_TRANSITION"}
    winners = [resp for resp in responses if resp.status_code == 200]
    losers = [resp for resp in responses if resp.status_code != 200]
    assert len(winners) == 1, [resp.status_code for resp in responses]
    assert len(losers) == 1, [resp.status_code for resp in responses]

    loser = losers[0]
    assert loser.status_code in expected_codes, loser.text
    assert loser.json()["error"]["code"] == expected_codes[loser.status_code]
    if loser.status_code == 422:
        # C-73: the message names the state the transition started from.
        assert "approved" in loser.json()["error"]["message"]

    # No duplicated side effects: only the winner dispatches a job. (The task
    # row itself is shared with the test's outer transaction — the fixture binds
    # every request session to one connection — so the committed state is not a
    # reliable observation here; the enqueue is.)
    assert [entry["task_id"] for entry in api.enqueued] == [str(task.id)]


# ── retry budget ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retrying_a_non_failed_task_returns_422_invalid_transition(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    task = await _seed_task(api, tenant, admin)

    resp = await _post(api, headers, str(task.id), "retry", None)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TRANSITION"
    assert "pending" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_retry_with_an_exhausted_budget_returns_409_max_retries_exceeded(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    task = await _seed_task(api, tenant, admin, status="failed", retry_count=3, max_retries=3)

    resp = await _post(api, headers, str(task.id), "retry", None)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "MAX_RETRIES_EXCEEDED"


@pytest.mark.asyncio
async def test_retry_below_the_budget_runs_the_task_again_and_dispatches(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    task = await _seed_task(api, tenant, admin, status="failed", retry_count=1, max_retries=3)

    resp = await _post(api, headers, str(task.id), "retry", None)

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["status"] == "executing"
    assert body["retry_count"] == 2
    assert [entry["task_id"] for entry in api.enqueued] == [str(task.id)]


# ── authorization and visibility ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_and_reject_are_admin_only(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    employee = await api.seed_user(tenant, role="employee")
    employee_headers = api.auth(employee, tenant)
    task = await _seed_task(api, tenant, admin, creator_id=employee.id)

    approve = await _post(api, employee_headers, str(task.id), "approve", {})
    assert approve.status_code == 403
    assert approve.json()["error"]["code"] == "FORBIDDEN"

    reject = await _post(api, employee_headers, str(task.id), "reject", {"reason": "no"})
    assert reject.status_code == 403
    assert reject.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_a_non_owner_member_cannot_retry_or_cancel_returns_403(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    creator = await api.seed_user(tenant, role="employee")
    other = await api.seed_user(tenant, role="employee")
    other_headers = api.auth(other, tenant)
    failed = await _seed_task(api, tenant, admin, status="failed", creator_id=creator.id)
    pending = await _seed_task(api, tenant, admin, creator_id=creator.id)

    retry = await _post(api, other_headers, str(failed.id), "retry", None)
    assert retry.status_code == 403
    assert retry.json()["error"]["code"] == "FORBIDDEN"

    cancel = await _post(api, other_headers, str(pending.id), "cancel", None)
    assert cancel.status_code == 403
    assert cancel.json()["error"]["code"] == "FORBIDDEN"

    # Reading stays open to any member of the tenant.
    assert (await _task_detail(api, other_headers, str(pending.id)))["status"] == "pending"
    assert (await api.client.get("/api/tasks", headers=other_headers)).status_code == 200


@pytest.mark.asyncio
async def test_tasks_are_tenant_scoped_and_malformed_identifiers_return_404(api) -> None:
    tenant_a, admin_a = await api.seed(role="agent_admin")
    tenant_b, admin_b = await api.seed(role="agent_admin")
    task = await _seed_task(api, tenant_a, admin_a)
    headers_b = api.auth(admin_b, tenant_b)

    detail = await api.client.get(f"/api/tasks/{task.id}", headers=headers_b)
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "TASK_NOT_FOUND"

    cancel = await _post(api, headers_b, str(task.id), "cancel", None)
    assert cancel.status_code == 404
    assert cancel.json()["error"]["code"] == "TASK_NOT_FOUND"

    malformed = await api.client.get(
        "/api/tasks/not-a-task-id", headers=api.auth(admin_a, tenant_a)
    )
    assert malformed.status_code == 404
    assert malformed.json()["error"]["code"] == "TASK_NOT_FOUND"

    listed_b = await api.client.get("/api/tasks", headers=headers_b)
    assert listed_b.status_code == 200
    assert listed_b.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_reject_records_the_reason_in_the_task_result(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    task = await _seed_task(api, tenant, admin)

    resp = await _post(api, headers, str(task.id), "reject", {"reason": "not approved"})

    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["status"] == "rejected"
    assert body["result"] == {"reason": "not approved"}


# ── dispatch semantics ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconciler_redispatches_an_unclaimed_row_and_a_duplicate_is_absorbed(
    api, monkeypatch, wait_for_job
) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await _insert_tool(api, tool_id="erp", tenant_id=tenant.id)
    calls = _install_proxy(api)
    task = await _seed_task(
        api, tenant, admin, status="approved", payload={"tool_id": "erp", "params": {"q": 1}}
    )
    outbox_id = str(uuid.uuid4())

    # A dispatch that was never claimed (crash between enqueue and claim) is
    # re-dispatched by the 15s reconciler: delivery is at least once.
    dispatched: list[str] = []

    def _record_enqueue(
        *, task_id: str, outbox_id: str, queue_name: str, payload: dict[str, Any]
    ) -> None:
        dispatched.append(outbox_id)
        Queue(queue_name, connection=api.redis_conn).enqueue(
            "app.workers.process_task.process_task",
            task_id,
            outbox_id,
            payload,
            job_id=outbox_id,
        )

    monkeypatch.setattr("app.services.task_reconcile.enqueue_process_task", _record_enqueue)
    async with api.db.session() as session:
        session.add(
            TaskOutboxEvent(
                id=outbox_id,
                task_id=str(task.id),
                type=_TOOL_APPROVAL_QUEUE,
                status="approved",
                payload=task.payload,
                created_at=datetime.now(UTC) - timedelta(seconds=30),
            )
        )
        await session.commit()

    await reconcile_undelivered(api.db.session)

    assert dispatched == [outbox_id]
    async with api.db.session() as session:
        row = await session.get(TaskOutboxEvent, outbox_id)
        assert row is not None
        assert row.delivered_at is not None
    queue = Queue(_TOOL_APPROVAL_QUEUE, connection=api.redis_conn)
    assert await wait_for_job(queue, outbox_id) is not None

    # The same dispatch delivered twice must execute the tool exactly once.
    proxy = api.app.state.tool_proxy
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", api.db.session)

    def _claim(row_id: str) -> bool:
        return _PROCESS_TASK_MODULE._claim_execution(row_id, client=api.redis_conn)

    await _PROCESS_TASK_MODULE._process(
        str(task.id), outbox_id, task.payload, tool_proxy=proxy, claim_execution=_claim
    )
    await _PROCESS_TASK_MODULE._process(
        str(task.id), outbox_id, task.payload, tool_proxy=proxy, claim_execution=_claim
    )

    assert len(calls) == 1
    detail = await _task_detail(api, api.auth(admin, tenant), str(task.id))
    assert detail["status"] == "completed"
    assert detail["result"]["status_code"] == 200


@pytest.mark.asyncio
async def test_reconciler_redispatches_at_most_one_batch_per_scan(api, monkeypatch) -> None:
    dispatched: list[str] = []

    def _record_enqueue(
        *, task_id: str, outbox_id: str, queue_name: str, payload: dict[str, Any]
    ) -> None:
        dispatched.append(outbox_id)

    monkeypatch.setattr("app.services.task_reconcile.enqueue_process_task", _record_enqueue)

    stale = datetime.now(UTC) - timedelta(seconds=30)
    row_ids = [str(uuid.uuid4()) for _ in range(101)]
    async with api.db.session() as session:
        for row_id in row_ids:
            session.add(
                TaskOutboxEvent(
                    id=row_id,
                    task_id=str(uuid.uuid4()),
                    type=_TOOL_APPROVAL_QUEUE,
                    status="approved",
                    payload={"tool_id": "erp"},
                    created_at=stale,
                )
            )
        await session.commit()

    await reconcile_undelivered(api.db.session)

    # One scan re-dispatches a single batch of 100 rows.
    assert len(dispatched) == 100
    async with api.db.session() as session:
        result = await session.execute(
            select(TaskOutboxEvent).where(
                TaskOutboxEvent.id.in_(row_ids),
                TaskOutboxEvent.delivered_at.is_(None),
            )
        )
        remaining = list(result.scalars().all())
    assert len(remaining) == 1
    assert remaining[0].id not in dispatched

    # The next scan picks up the leftover row — no permanent loss.
    await reconcile_undelivered(api.db.session)
    assert len(dispatched) == 101
    assert set(dispatched) == set(row_ids)


@pytest.mark.asyncio
async def test_gated_tool_is_not_called_before_approval_and_creates_a_high_priority_task(
    api, monkeypatch
) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="agent-1")
    await _insert_tool(
        api,
        tool_id="hr-search",
        tenant_id=tenant.id,
        risk_level="high",
        permission_mode="confirm",
        auth_type="bearer",
        auth_config={"token": "tok-hr-search"},
    )
    calls = _install_proxy(api)
    # The running conversation service must use the recording proxy.
    api.app.state.conversation_service = ConversationService(
        api.settings, tool_proxy=api.app.state.tool_proxy
    )
    headers = api.auth(admin, tenant)

    conversation = await api.client.post(
        "/api/conversations", headers=headers, json={"agentId": "agent-1"}
    )
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["data"]["id"]

    frames = [
        _frame(
            "agent_thought",
            {
                "id": "th1",
                "thought": "querying headcount",
                "tool": "hr-search",
                "tool_input": '{"q": "headcount"}',
                "observation": "",
            },
        ),
        _frame("message_end", {"id": "trace1", "conversation_id": "dify-c1", "metadata": {}}),
    ]
    monkeypatch.setattr(
        _CONVERSATION_MODULE, "DifyClientService", lambda *args, **kwargs: _FakeDifyClient(frames)
    )

    resp = await api.client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers=headers,
        json={"query": "how many headcount?", "agentId": "agent-1"},
    )

    assert resp.status_code == 200, resp.text
    approval = _sse_data(resp.text, "tool_approval_required")
    assert approval["toolName"] == "hr-search"

    # Awaiting approval means the tool has not been called at all.
    assert calls == []
    detail = await _task_detail(api, headers, approval["taskId"])
    assert detail["status"] == "pending"
    assert detail["type"] == "tool_approval"
    assert detail["payload"]["tool_id"] == "hr-search"
    # C-66: the tool credential stays on the outbound request — the approval
    # task carries the tool reference and the caller's params only.
    assert "tok-hr-search" not in json.dumps(detail["payload"])
    # A tool-approval task is high priority and expires 24h after creation.
    assert detail["priority"] == "high"
    lifetime = datetime.fromisoformat(detail["expires_at"]) - datetime.fromisoformat(
        detail["created_at"]
    )
    assert abs(lifetime - timedelta(hours=24)) < timedelta(minutes=1)
