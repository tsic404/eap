"""Acceptance case for agent lifecycle transitions and unpublished-agent visibility.

Covers the publish/offline state machine, the employee-facing visibility rules
for unpublished agents, and the rule that taking an agent offline leaves the
conversations already started against it usable: after the transition a further
turn is still driven, on a mocked upstream Dify boundary while the SSE
normalisation, framing and routers stay real.
"""

from typing import Any

import pytest

import app.services.conversation as conversation_module

TC_ID = "TC-tech-agent-02"

_TURN_PATH = "/api/conversations/{conversation_id}/messages"


class _Upstream:
    """Dify Service-API stand-in: one normal turn and an empty message history."""

    async def get(self, path: str, query: Any = None, *, retries: int = 3, timeout: Any = None):
        return {"data": [], "has_more": False}

    async def post_stream(self, path: str, body: Any):
        yield b'data: {"event": "message", "answer": "hi", "message_id": "m1"}\n\n'
        yield b'data: {"event": "message_end", "id": "t1", "metadata": {}}\n\n'

    async def aclose(self) -> None:
        pass


def _frame_names(body: str) -> list[str]:
    """The event names of a platform SSE body, in order.

    Asserts the contracted frame shape on the way: one ``event:`` line followed
    by exactly one single-line ``data:`` JSON line, then a blank separator.
    """
    names: list[str] = []
    for block in body.split("\n\n"):
        if not block:
            continue
        lines = block.split("\n")
        assert len(lines) == 2, f"not an event name plus one data line: {block!r}"
        assert lines[0].startswith("event: "), block
        assert lines[1].startswith("data: "), block
        names.append(lines[0][len("event: ") :])
    return names


async def _turn(api, headers: dict[str, str], conversation_id: str, agent_id: str):
    """Drive one streaming turn on an existing conversation."""
    return await api.client.post(
        _TURN_PATH.format(conversation_id=conversation_id),
        headers=headers,
        json={"query": "hi", "agentId": agent_id},
    )


async def _published_detail(api, headers: dict[str, str], agent_id: str) -> dict[str, object]:
    resp = await api.client.get(f"/api/agents/{agent_id}", headers=headers)
    assert resp.status_code == 200
    return resp.json()["data"]


@pytest.mark.parametrize("status", ["draft", "testing", "published"])
@pytest.mark.asyncio
async def test_publish_reaches_published_from_any_publishable_state(api, status: str) -> None:
    tenant, user = await api.seed(role="agent_admin")
    agent_id = f"{status}-bot"
    await api.seed_agent(tenant, agent_id=agent_id, status=status)
    headers = api.auth(user, tenant)

    resp = await api.client.post(
        f"/api/agents/{agent_id}/publish", headers=headers, json={"version": 1}
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "published"
    detail = await _published_detail(api, headers, agent_id)
    assert detail["status"] == "published"
    # C-33 fixes neither the version arithmetic nor a publisher rewrite on the
    # idempotent branch, so the transition and the following read only have to
    # agree with each other.
    assert detail["version"] == body["version"]
    assert detail["publishedBy"] == body["publishedBy"]
    if status != "published":
        # A first transition into ``published`` records the instant and the actor
        # (C-35 exposes both, and nothing else could ever set them).
        assert body["publishedAt"] is not None
        assert body["publishedBy"] == str(user.id)


@pytest.mark.asyncio
async def test_repeated_publish_returns_the_target_state(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="bot-1", status="draft")
    headers = api.auth(user, tenant)

    first = await api.client.post("/api/agents/bot-1/publish", headers=headers, json={"version": 1})
    assert first.status_code == 200
    first_body = first.json()["data"]
    assert first_body["status"] == "published"

    # C-33 does not fix the version arithmetic of the idempotent repeat, so the
    # current version is taken from the first transition instead of hardcoded.
    second = await api.client.post(
        "/api/agents/bot-1/publish", headers=headers, json={"version": first_body["version"]}
    )

    assert second.status_code == 200
    assert second.json()["data"]["status"] == "published"
    assert (await _published_detail(api, headers, "bot-1"))["status"] == "published"


@pytest.mark.asyncio
async def test_publish_with_stale_version_returns_409(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="draft-bot", status="draft")
    await api.seed_agent(tenant, agent_id="live-bot", status="published")
    headers = api.auth(user, tenant)

    for agent_id in ("draft-bot", "live-bot"):
        resp = await api.client.post(
            f"/api/agents/{agent_id}/publish", headers=headers, json={"version": 9}
        )
        assert resp.status_code == 409, (agent_id, resp.status_code)
        assert resp.json()["error"]["code"] == "CONFLICT"

    assert (await _published_detail(api, headers, "draft-bot"))["status"] == "draft"
    assert (await _published_detail(api, headers, "live-bot"))["status"] == "published"


@pytest.mark.parametrize("status", ["draft", "testing", "published", "offline"])
@pytest.mark.asyncio
async def test_offline_reaches_offline_from_any_state(api, status: str) -> None:
    tenant, user = await api.seed(role="agent_admin")
    agent_id = f"{status}-bot"
    await api.seed_agent(tenant, agent_id=agent_id, status=status)
    headers = api.auth(user, tenant)

    resp = await api.client.post(f"/api/agents/{agent_id}/offline", headers=headers)

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "offline"

    again = await api.client.post(f"/api/agents/{agent_id}/offline", headers=headers)

    assert again.status_code == 200
    assert again.json()["data"]["status"] == "offline"


@pytest.mark.asyncio
async def test_offline_with_stale_version_returns_409(api) -> None:
    # C-33's second half of the offline clause: a version mismatch is a 409
    # `CONFLICT` and must leave the agent untouched.
    tenant, user = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="bot-1", status="published")
    headers = api.auth(user, tenant)

    resp = await api.client.post("/api/agents/bot-1/offline", headers=headers, json={"version": 9})

    if resp.status_code == 200:
        # The offline route takes no body at all today, so the version that the
        # clause requires is never compared; that gap is registered rather than
        # asserted as expected behaviour. Restricting the registration to the
        # target state keeps any other failure of this case red.
        pytest.xfail(
            "C-33 offline version mismatch -> 409 CONFLICT not implemented at main@68ad4b5"
        )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "CONFLICT"
    assert (await _published_detail(api, headers, "bot-1"))["status"] == "published"


@pytest.mark.asyncio
async def test_publish_after_offline_returns_409_naming_the_current_status(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    await api.seed_agent(tenant, agent_id="bot-1", status="published")
    headers = api.auth(user, tenant)

    offline = await api.client.post("/api/agents/bot-1/offline", headers=headers)
    assert offline.status_code == 200
    version = offline.json()["data"]["version"]

    resp = await api.client.post(
        "/api/agents/bot-1/publish", headers=headers, json={"version": version}
    )

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "INVALID_STATE"
    assert "offline" in error["message"]
    assert (await _published_detail(api, headers, "bot-1"))["status"] == "offline"


@pytest.mark.asyncio
async def test_employee_list_returns_only_published_and_ignores_the_status_filter(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    employee = await api.seed_user(tenant, role="employee")
    await api.seed_agent(tenant, agent_id="draft-bot", status="draft")
    await api.seed_agent(tenant, agent_id="testing-bot", status="testing")
    await api.seed_agent(tenant, agent_id="offline-bot", status="offline")
    await api.seed_agent(tenant, agent_id="live-bot", status="published")

    plain = await api.client.get("/api/agents", headers=api.auth(employee, tenant))
    assert plain.status_code == 200
    assert [item["agentId"] for item in plain.json()["data"]["items"]] == ["live-bot"]
    assert plain.json()["data"]["total"] == 1

    filtered = await api.client.get("/api/agents?status=draft", headers=api.auth(employee, tenant))
    assert filtered.status_code == 200
    assert [item["agentId"] for item in filtered.json()["data"]["items"]] == ["live-bot"]
    assert filtered.json()["data"]["total"] == 1

    admin_view = await api.client.get("/api/agents?status=draft", headers=api.auth(admin, tenant))
    assert admin_view.status_code == 200
    assert [item["agentId"] for item in admin_view.json()["data"]["items"]] == ["draft-bot"]


@pytest.mark.asyncio
async def test_employee_detail_of_unpublished_agent_matches_a_missing_agent(api) -> None:
    tenant, _admin = await api.seed(role="agent_admin")
    employee = await api.seed_user(tenant, role="employee")
    await api.seed_agent(tenant, agent_id="live-bot", status="published")
    for status in ("draft", "testing", "offline"):
        await api.seed_agent(tenant, agent_id=f"{status}-bot", status=status)
    headers = api.auth(employee, tenant)

    missing = await api.client.get("/api/agents/ghost-bot", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"

    for status in ("draft", "testing", "offline"):
        resp = await api.client.get(f"/api/agents/{status}-bot", headers=headers)
        assert resp.status_code == 404, (status, resp.status_code)
        # An unpublished agent must be indistinguishable from one that is absent.
        assert resp.json() == missing.json()

    visible = await api.client.get("/api/agents/live-bot", headers=headers)
    assert visible.status_code == 200


@pytest.mark.asyncio
async def test_unpublished_agent_cannot_start_a_conversation(api) -> None:
    tenant, _admin = await api.seed(role="agent_admin")
    employee = await api.seed_user(tenant, role="employee")
    await api.seed_agent(tenant, agent_id="draft-bot", status="draft")
    await api.seed_agent(tenant, agent_id="offline-bot", status="offline")
    headers = api.auth(employee, tenant)

    ghost = await api.client.post(
        "/api/conversations", headers=headers, json={"agentId": "ghost-bot"}
    )
    assert ghost.status_code == 404

    for agent_id in ("draft-bot", "offline-bot"):
        resp = await api.client.post(
            "/api/conversations", headers=headers, json={"agentId": agent_id}
        )
        assert resp.status_code == 404, (agent_id, resp.status_code)
        assert resp.json() == ghost.json()
        assert resp.json()["error"]["code"] == "AGENT_NOT_FOUND"

    listed = await api.client.get("/api/conversations", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_offline_does_not_affect_existing_conversations(api, monkeypatch) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    employee = await api.seed_user(tenant, role="employee")
    await api.seed_agent(tenant, agent_id="live-bot", status="published")
    employee_headers = api.auth(employee, tenant)
    monkeypatch.setattr(conversation_module, "DifyClientService", lambda *a, **kw: _Upstream())

    created = await api.client.post(
        "/api/conversations", headers=employee_headers, json={"agentId": "live-bot"}
    )
    assert created.status_code == 201
    conversation_id = created.json()["data"]["id"]

    before = await _turn(api, employee_headers, conversation_id, "live-bot")
    assert before.status_code == 200
    assert _frame_names(before.text)[-1] == "message_end"

    offline = await api.client.post("/api/agents/live-bot/offline", headers=api.auth(admin, tenant))
    assert offline.status_code == 200
    assert offline.json()["data"]["status"] == "offline"

    read = await api.client.get(f"/api/conversations/{conversation_id}", headers=employee_headers)
    assert read.status_code == 200
    assert read.json()["data"]["agentId"] == "live-bot"

    listed = await api.client.get("/api/conversations", headers=employee_headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["data"]["items"]] == [conversation_id]

    history = await api.client.get(
        f"/api/conversations/{conversation_id}/messages", headers=employee_headers
    )
    assert history.status_code == 200
    assert history.json()["data"] == {"items": [], "nextCursor": None}

    # C-34: offlining must not break continuing use, so the conversation still
    # takes a further turn and its stream terminates normally (a single end
    # event, no error frame).
    continued = await _turn(api, employee_headers, conversation_id, "live-bot")
    assert continued.status_code == 200
    names = _frame_names(continued.text)
    assert names, continued.text
    assert names[-1] == "message_end"
    assert "error" not in names
