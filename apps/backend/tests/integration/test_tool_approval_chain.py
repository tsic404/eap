"""End-to-end tool-approval chain smoke test (§32.7 / §33.7).

Drives the full path against real PostgreSQL + Redis (testcontainers), mocking
only the two boundaries the issue scopes out: the Dify HTTP stream and the
ToolProxy HTTP call. The sequence is:

    chat SSE ``agent_thought`` (gated tool) → ``tool_approval_required`` event
    → pending ``tool_approval`` task → ``POST /api/tasks/{id}/approve``
    → ``TaskService.transition`` writes outbox + enqueues RQ → worker completes
    → task ``result`` records the tool response.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from rq import Queue

import app.services.conversation as conversation_module
from app.services.tool_proxy import ToolProxy

_PROCESS_TASK_MODULE = importlib.import_module("app.workers.process_task")


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


@pytest.mark.asyncio
async def test_tool_approval_chain_end_to_end(api, monkeypatch, wait_for_job) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="agent-1")
    await api.seed_tool(tenant, tool_id="hr-search", risk_level="high", permission_mode="confirm")
    headers = api.auth(admin, tenant)

    # 1. A turn whose agent_thought names the gated tool must surface approval.
    #    Dify sends a "before" frame (tool_input, empty observation) and an
    #    "after" echo (observation, no tool_input) for one call; only the first
    #    may create a pending task.
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
        _frame(
            "agent_thought",
            {
                "id": "th1",
                "thought": "querying headcount",
                "tool": "hr-search",
                "observation": "headcount=42",
            },
        ),
        _frame("message_end", {"id": "trace1", "conversation_id": "dify-c1", "metadata": {}}),
    ]

    def make_client(*args: Any, **kwargs: Any) -> _FakeDifyClient:
        return _FakeDifyClient(frames)

    monkeypatch.setattr(conversation_module, "DifyClientService", make_client)

    created = await api.client.post(
        "/api/conversations", headers=headers, json={"agentId": "agent-1"}
    )
    assert created.status_code == 201
    conv_id = created.json()["data"]["id"]

    resp = await api.client.post(
        f"/api/conversations/{conv_id}/messages",
        headers=headers,
        json={"query": "how many headcount?", "agentId": "agent-1"},
    )
    assert resp.status_code == 200
    # The after-call echo must not add a second approval for the same call.
    assert resp.text.count("event: tool_approval_required") == 1

    approval = _sse_data(resp.text, "tool_approval_required")
    assert approval["toolName"] == "hr-search"
    assert approval["params"] == {"q": "headcount"}
    task_id = approval["taskId"]

    # 2. The pending approval task is visible through the task center.
    listed = await api.client.get(
        "/api/tasks", headers=headers, params={"type": "tool_approval", "status": "pending"}
    )
    assert listed.status_code == 200
    items = listed.json()["data"]["items"]
    assert [item["id"] for item in items] == [task_id]
    assert items[0]["status"] == "pending"

    # 3. Approving runs the real transition: outbox row + RQ enqueue.
    approved = await api.client.post(f"/api/tasks/{task_id}/approve", headers=headers, json={})
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "approved"

    assert api.enqueued, "approval must dispatch the worker job"
    enqueued = api.enqueued[-1]
    assert enqueued["task_id"] == task_id
    assert enqueued["queue_name"] == "tool-approval"

    queue = Queue(enqueued["queue_name"], connection=api.redis_conn)
    job = await wait_for_job(queue, enqueued["outbox_id"])
    assert job is not None, "worker job must be present on the RQ queue"

    # 4. Execute the dispatched worker body. The RQ entrypoint
    #    ``process_task`` spawns a fresh event loop (``asyncio.run``), which the
    #    fixture's single-loop transaction cannot host, so drive ``_process``
    #    directly with the job's own args and a mocked ToolProxy.
    proxy = ToolProxy(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))
    )
    monkeypatch.setattr(_PROCESS_TASK_MODULE, "async_session_factory", api.db.session)
    await _PROCESS_TASK_MODULE._process(*job.args, tool_proxy=proxy, claim_execution=lambda _: True)

    # 5. The task is completed and carries the tool response.
    final = await api.client.get(f"/api/tasks/{task_id}", headers=headers)
    assert final.status_code == 200
    body = final.json()["data"]
    assert body["status"] == "completed"
    assert body["result"]["status_code"] == 200
    assert body["result"]["body"] == {"ok": True}
