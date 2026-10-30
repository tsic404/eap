"""TC-tech-agent-01 — the agent surface: creation, locking, deletion, bindings.

Two facets of the case share this module. The first drives the public
``/api/agents`` surface end to end — creation, binding validation, optimistic
locking and deletion — with the Dify Console boundary mocked on ``api.console``,
PostgreSQL and Redis real, and every test seeding its own tenant. The second
pins ``docs/02-contracts.md`` C-102 / C-14 / C-98 with scope item F1: a binding
may only reference an asset of the agent's own tenant, or a global tool that
carries no tenant at all (C-65); a cross-tenant id is answered exactly like a
missing one and leaves no half-written row behind. Both facets serve
``docs/03-acceptance.md`` TC-tech-agent-01 → AC-T03.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select

import app.services.conversation as conversation_module
from app.config import Settings
from app.db import get_session
from app.dify_console import DifyConsoleClient, DifyConsoleError
from app.main import create_app
from app.models.agent import AgentKnowledgeBinding, AgentRegistry, AgentToolBinding
from app.models.conversation import Conversation
from app.models.knowledge import KnowledgeBaseRegistry
from app.models.tenant import Tenant
from app.models.tool import ToolRegistry
from app.models.user import User

# The future import and the imports above must precede every other statement;
# the case identifier leads the module body from here on.
TC_ID = "TC-tech-agent-01"

# ── the create / patch / delete facet of the case (shared ``api`` fixture) ──


# C-35's field inventory for the agent domain (camelCase per C-11): identifier,
# tenant, name, description, type, category, icon, tags, status, model
# configuration, prompt, version, creator, publish instant and publisher, create
# and update instants. The upstream app id and app key are declared private.
_CONTRACTED_AGENT_FIELDS = frozenset(
    {
        "agentId",
        "tenantId",
        "name",
        "description",
        "type",
        "category",
        "icon",
        "tags",
        "status",
        "modelId",
        "modelName",
        "modelProvider",
        "prompt",
        "version",
        "createdBy",
        "publishedAt",
        "publishedBy",
        "createdAt",
        "updatedAt",
    }
)
_CONTRACTED_DETAIL_FIELDS = frozenset(
    {"boundKnowledge", "boundTools", "recentLogs", "conversationCount"}
)


def _payload(agent_id: str = "sales-bot", **overrides: object) -> dict[str, object]:
    """A minimal valid create body; ``overrides`` replaces individual fields."""
    body: dict[str, object] = {"agentId": agent_id, "name": "Sales Bot"}
    body.update(overrides)
    return body


async def _list_agent_ids(api, headers: dict[str, str]) -> list[str]:
    resp = await api.client.get("/api/agents", headers=headers)
    assert resp.status_code == 200
    return [item["agentId"] for item in resp.json()["data"]["items"]]


async def _detail(api, headers: dict[str, str], agent_id: str) -> dict[str, object]:
    resp = await api.client.get(f"/api/agents/{agent_id}", headers=headers)
    assert resp.status_code == 200
    return resp.json()["data"]


async def _create(api, headers: dict[str, str], body: dict[str, object]) -> object:
    return await api.client.post("/api/agents", headers=headers, json=body)


@pytest.mark.asyncio
async def test_create_returns_201_draft_version_one_with_creator_and_tenant(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(api, headers, _payload())

    assert resp.status_code == 201
    body = resp.json()["data"]
    assert body["agentId"] == "sales-bot"
    assert body["name"] == "Sales Bot"
    assert body["status"] == "draft"
    assert body["version"] == 1
    assert body["createdBy"] == str(user.id)
    assert body["tenantId"] == str(tenant.id)
    assert body["publishedAt"] is None
    # The stored row is readable again through the detail read.
    assert (await _detail(api, headers, "sales-bot"))["version"] == 1


@pytest.mark.asyncio
async def test_create_with_a_model_configures_the_engine_with_a_nested_model_object(api) -> None:
    """C-31/AC-08: the model selection reaches the engine as the object it validates."""
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload(
            "model-bot",
            modelProvider="openai",
            modelId="gpt-4o",
            prompt="You are helpful",
        ),
    )

    assert resp.status_code == 201, resp.text
    config = api.console.configure_model.await_args.args[1]
    # C-125: the prompt is the system prompt; nothing is sent as the greeting.
    assert config.model_dump(exclude_none=True) == {
        "model": {"provider": "openai", "name": "gpt-4o", "completion_params": {}},
        "pre_prompt": "You are helpful",
    }


@pytest.mark.asyncio
async def test_create_resolves_the_provider_key_the_form_suggests(api) -> None:
    """C-126/AC-T03: ``azure`` resolves to the installed plugin identifier."""
    api.console.get_model_providers.return_value = {
        "data": [
            {"provider": "langgenius/openai/openai"},
            {"provider": "langgenius/azure_openai/azure_openai"},
        ]
    }
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload("azure-bot", modelProvider="azure", modelId="gpt-4o"),
    )

    assert resp.status_code == 201, resp.text
    configured = api.console.configure_model.await_args.args[1]
    assert configured.model.provider == "langgenius/azure_openai/azure_openai"

    # A full identifier passes through untouched.
    api.console.configure_model.reset_mock()
    resp = await _create(
        api,
        headers,
        _payload(
            "full-id-bot",
            modelProvider="langgenius/azure_openai/azure_openai",
            modelId="gpt-4o",
        ),
    )

    assert resp.status_code == 201, resp.text
    configured = api.console.configure_model.await_args.args[1]
    assert configured.model.provider == "langgenius/azure_openai/azure_openai"


@pytest.mark.asyncio
async def test_create_rejects_an_uninstalled_provider_without_side_effects(api) -> None:
    """C-126/AC-T03: an unknown provider is a 400 before the engine is touched."""
    api.console.get_model_providers.return_value = {"data": [{"provider": "openai"}]}
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload("ghost-bot", modelProvider="azure", modelId="gpt-4o"),
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_MODEL_PROVIDER"
    api.console.create_app.assert_not_awaited()
    assert "ghost-bot" not in await _list_agent_ids(api, headers)


@pytest.mark.parametrize(
    "model_fields",
    [{"modelProvider": "azure"}, {"modelId": "gpt-4o"}],
    ids=["provider-only", "model-id-only"],
)
@pytest.mark.asyncio
async def test_create_with_only_one_model_field_skips_model_configuration(
    api, model_fields
) -> None:
    """C-126/AC-T03: a lone model field is stored, never resolved or configured."""
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(api, headers, _payload("partial-model-bot", **model_fields))

    assert resp.status_code == 201, resp.text
    api.console.get_model_providers.assert_not_awaited()
    api.console.configure_model.assert_not_awaited()


# ── C-126/AC-T03 provider resolution: every accepted and rejected input ─────


def _installed(*providers: str) -> dict[str, object]:
    """The console listing for a workspace with exactly ``providers`` installed."""
    return {"data": [{"provider": provider} for provider in providers]}


@pytest.mark.asyncio
async def test_create_treats_a_full_identifier_as_decisive(api) -> None:
    """C-126/AC-T03: a full-identifier hit wins even when its segments repeat."""
    api.console.get_model_providers.return_value = _installed(
        "langgenius/openai/openai", "acme/openai/legacy"
    )
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload(
            "full-id-exact-bot",
            modelProvider="langgenius/openai/openai",
            modelId="gpt-4o",
        ),
    )

    assert resp.status_code == 201, resp.text
    configured = api.console.configure_model.await_args.args[1]
    assert configured.model.provider == "langgenius/openai/openai"


@pytest.mark.asyncio
async def test_create_prefers_an_exact_segment_over_a_longer_prefix(api) -> None:
    """C-126/AC-T03: a segment-equal provider wins over a prefix-only one."""
    api.console.get_model_providers.return_value = _installed(
        "langgenius/openai/openai", "langgenius/openai_compatible/openai_compatible"
    )
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload("exact-segment-bot", modelProvider="openai", modelId="gpt-4o"),
    )

    # ``openai`` equals a segment of the first provider and is only a prefix of
    # the second, so the exact layer decides; a prefix-only implementation sees
    # two candidates and answers 400.
    assert resp.status_code == 201, resp.text
    configured = api.console.configure_model.await_args.args[1]
    assert configured.model.provider == "langgenius/openai/openai"


@pytest.mark.asyncio
async def test_create_rejects_a_provider_matching_two_installed_segments(api) -> None:
    """C-126/AC-T03: two segment-equal providers leave the input undecidable."""
    api.console.get_model_providers.return_value = _installed(
        "langgenius/openai/openai", "acme/openai/legacy"
    )
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload("segment-tie-bot", modelProvider="openai", modelId="gpt-4o"),
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_MODEL_PROVIDER"
    api.console.create_app.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_rejects_a_case_variant_of_an_installed_provider(api) -> None:
    """C-126/AC-T03: matching is case-sensitive, so folding is not accepted."""
    api.console.get_model_providers.return_value = _installed("langgenius/openai/openai")
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload("case-bot", modelProvider="OPENAI", modelId="gpt-4o"),
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_MODEL_PROVIDER"
    api.console.create_app.assert_not_awaited()


@pytest.mark.parametrize(
    "provider",
    ["open ai", "langgenius / openai / openai"],
    ids=["inside-a-segment", "around-a-separator"],
)
@pytest.mark.asyncio
async def test_create_rejects_whitespace_inside_a_provider_identifier(api, provider) -> None:
    """C-126/AC-T03: only whitespace around the whole input is ignored."""
    api.console.get_model_providers.return_value = _installed("langgenius/openai/openai")
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload("inner-space-bot", modelProvider=provider, modelId="gpt-4o"),
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_MODEL_PROVIDER"
    api.console.create_app.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_rejects_a_whitespace_only_provider(api) -> None:
    """C-126/AC-T03: an input empty after trimming never resolves."""
    api.console.get_model_providers.return_value = _installed("langgenius/openai/openai")
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload("blank-provider-bot", modelProvider="   ", modelId="gpt-4o"),
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_MODEL_PROVIDER"
    api.console.create_app.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_ignores_whitespace_around_a_provider(api) -> None:
    """C-126/AC-T03: surrounding whitespace is ignored, so the key still resolves."""
    api.console.get_model_providers.return_value = _installed(
        "langgenius/azure_openai/azure_openai"
    )
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload("padded-bot", modelProvider="  azure  ", modelId="gpt-4o"),
    )

    assert resp.status_code == 201, resp.text
    configured = api.console.configure_model.await_args.args[1]
    assert configured.model.provider == "langgenius/azure_openai/azure_openai"


@pytest.mark.asyncio
async def test_create_with_an_empty_provider_listing_is_a_client_error(api) -> None:
    """C-126/AC-T03: an empty but readable listing is a miss, not an upstream failure."""
    api.console.get_model_providers.return_value = {"data": []}
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload("empty-listing-bot", modelProvider="openai", modelId="gpt-4o"),
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_MODEL_PROVIDER"
    api.console.create_app.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_maps_a_provider_listing_failure_to_502(api) -> None:
    """C-126/AC-T03: only a listing read failure is an upstream error."""
    api.console.get_model_providers.side_effect = DifyConsoleError(503, "engine unavailable")
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload("listing-down-bot", modelProvider="openai", modelId="gpt-4o"),
    )

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "DIFY_ERROR"
    api.console.create_app.assert_not_awaited()


# ── C-125/AC-T03 prompt: the observable system-prompt surface ───────────────


@pytest.mark.asyncio
async def test_create_without_a_prompt_sends_no_system_prompt(api) -> None:
    """C-125/AC-T03: no prompt submitted, no system prompt on the engine config."""
    api.console.get_model_providers.return_value = _installed("langgenius/openai/openai")
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(
        api,
        headers,
        _payload("silent-bot", modelProvider="openai", modelId="gpt-4o"),
    )

    assert resp.status_code == 201, resp.text
    config = api.console.configure_model.await_args.args[1]
    # "Empty" covers both an omitted key and an explicit empty string.
    assert config.pre_prompt in (None, "")
    assert config.opening_statement in (None, "")


class _PromptFollowingUpstream:
    """Dify Service-API stand-in that answers out of the app's system prompt.

    The engine is mocked, so the reply is derived from the prompt the creation
    payload actually carried: only when it equals the submitted instruction does
    the stand-in answer with the fixed prefix. Redirect the prompt elsewhere and
    the observable answer changes, so this is not a constant.
    """

    def __init__(
        self,
        captured: dict[str, object],
        configured_prompt: str | None,
        expected_prompt: str,
    ) -> None:
        self._captured = captured
        self._configured_prompt = configured_prompt
        self._expected_prompt = expected_prompt

    @property
    def _answer(self) -> str:
        if self._configured_prompt == self._expected_prompt:
            return "OK：收到"
        return "提示未生效"

    async def post_stream(self, path: str, body: object):
        self._captured["path"] = path
        self._captured["body"] = body
        events = [
            {"event": "message", "answer": self._answer, "message_id": "m1"},
            {"event": "message_end", "id": "t1", "metadata": {}},
        ]
        for event in events:
            yield f"data: {json.dumps(event)}\n\n".encode()

    async def get(
        self, path: str, query: object = None, *, retries: int = 3, timeout: object = None
    ):
        return {"data": [], "has_more": False}

    async def aclose(self) -> None:
        pass


def _first_message_content(body: str) -> str:
    for block in body.split("\n\n"):
        lines = block.split("\n")
        if len(lines) == 2 and lines[0] == "event: message":
            return json.loads(lines[1][len("data: ") :])["content"]
    raise AssertionError(f"no message frame in {body!r}")


@pytest.mark.asyncio
async def test_a_fixed_prefix_prompt_is_the_system_prompt_behind_the_answer(
    api, monkeypatch
) -> None:
    """C-125/AC-T03: the submitted instruction is the app-level system prompt.

    The creation payload carries it as the engine's system prompt, the
    conversation turn adds no competing prompt, and the engine answer reaches
    the client. Whether the model truly obeys needs a real engine.
    """
    api.console.get_model_providers.return_value = _installed("langgenius/openai/openai")
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    instruction = "只以 OK 开头回答"

    created = await _create(
        api,
        headers,
        _payload(
            "prefix-bot",
            modelProvider="openai",
            modelId="gpt-4o",
            prompt=instruction,
        ),
    )
    assert created.status_code == 201, created.text
    config = api.console.configure_model.await_args.args[1]
    assert config.pre_prompt == instruction

    published = await api.client.post(
        "/api/agents/prefix-bot/publish", headers=headers, json={"version": 1}
    )
    assert published.status_code == 200, published.text

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        conversation_module,
        "DifyClientService",
        lambda *args, **kwargs: _PromptFollowingUpstream(
            captured, config.pre_prompt, instruction
        ),
    )
    conversation = await api.client.post(
        "/api/conversations", headers=headers, json={"agentId": "prefix-bot"}
    )
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["data"]["id"]

    streamed = await api.client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers=headers,
        json={"query": "hi", "agentId": "prefix-bot"},
    )
    assert streamed.status_code == 200, streamed.text
    assert _first_message_content(streamed.text).startswith("OK")
    upstream_body = captured["body"]
    assert isinstance(upstream_body, dict)
    # The instruction must not compete with the app-level prompt: neither
    # concatenated into the query nor smuggled into the inputs map.
    assert upstream_body["query"] == "hi"
    assert set(upstream_body["inputs"]) == {
        "tenant_id",
        "user_id",
        "user_preferences",
        "user_facts",
    }
    assert not [key for key in upstream_body if "prompt" in key]


@pytest.mark.asyncio
async def test_agent_responses_never_carry_the_upstream_app_key(api) -> None:
    """C-35/C-109: the upstream application key never appears in a response.

    The console mock hands back ``{"token": "app-secret-1"}``, which the service
    stores as ``dify_api_key``; that value (and its field name) must stay
    server-side on create, detail and list alike.
    """
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    upstream_key = api.console.create_api_key.return_value["token"]

    created = await _create(api, headers, _payload("key-bot"))
    assert created.status_code == 201
    detail = await api.client.get("/api/agents/key-bot", headers=headers)
    assert detail.status_code == 200
    listed = await api.client.get("/api/agents", headers=headers)
    assert listed.status_code == 200

    for response in (created, detail, listed):
        assert "difyApiKey" not in response.text
        assert upstream_key not in response.text


@pytest.mark.asyncio
async def test_agent_responses_expose_only_the_contracted_fields(api) -> None:
    """C-35/C-109: no payload carries the upstream application id either.

    C-35 enumerates the exposed agent fields and declares the upstream app id and
    app key server-private, so the payload key set is the contracted one.
    """
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    created = await _create(api, headers, _payload("shape-bot"))
    assert created.status_code == 201
    detail = await api.client.get("/api/agents/shape-bot", headers=headers)
    assert detail.status_code == 200
    listed = await api.client.get("/api/agents", headers=headers)
    assert listed.status_code == 200

    payloads = [
        created.json()["data"],
        detail.json()["data"],
        *listed.json()["data"]["items"],
    ]
    for payload in payloads:
        assert set(payload) <= _CONTRACTED_AGENT_FIELDS | _CONTRACTED_DETAIL_FIELDS, sorted(payload)
        assert "difyAppId" not in payload
        assert "difyApiKey" not in payload


@pytest.mark.asyncio
async def test_create_accepts_identifier_boundary_lengths(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    for agent_id in ("abc", "a-b-c", "a" * 64):
        resp = await _create(api, headers, _payload(agent_id))
        assert resp.status_code == 201, (agent_id, resp.text)


@pytest.mark.asyncio
async def test_create_rejects_invalid_identifiers_with_422(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    for agent_id in ("ab", "a", "ABC", "-abc", "abc-", "ab_c", "ab c", "a" * 65, ""):
        resp = await _create(api, headers, _payload(agent_id))
        assert resp.status_code == 422, (agent_id, resp.status_code)
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    assert await _list_agent_ids(api, headers) == []


@pytest.mark.asyncio
async def test_create_rejects_field_constraint_violations_with_422(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    # C-31 bounds the length of name/description/category and the tag count
    # without fixing the exact limits, so the probes sit far beyond any plausible
    # limit instead of on the implementation's own boundary.
    bodies: list[dict[str, object]] = [
        {"agentId": "field-bot"},  # name is mandatory
        _payload("field-bot", name=""),
        _payload("field-bot", name="n" * 10_000),
        _payload("field-bot", description="d" * 10_000),
        _payload("field-bot", category="c" * 10_000),
        _payload("field-bot", tags=[f"tag-{i}" for i in range(1_000)]),
    ]

    for body in bodies:
        resp = await _create(api, headers, body)
        assert resp.status_code == 422, (body, resp.status_code)
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    assert await _list_agent_ids(api, headers) == []


@pytest.mark.asyncio
async def test_create_with_duplicate_agent_id_in_the_same_tenant_returns_409(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    first = await _create(api, headers, _payload("dup-bot"))
    assert first.status_code == 201

    second = await _create(api, headers, _payload("dup-bot"))

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DUPLICATE_AGENT_ID"
    assert await _list_agent_ids(api, headers) == ["dup-bot"]


@pytest.mark.asyncio
async def test_same_agent_id_is_reusable_in_another_tenant(api) -> None:
    # C-103 scopes agent-id uniqueness to the tenant, so a second tenant may use
    # the same identifier.
    tenant_a, admin_a = await api.seed(role="agent_admin")
    tenant_b, admin_b = await api.seed(role="agent_admin")

    first = await _create(api, api.auth(admin_a, tenant_a), _payload("shared-bot"))
    assert first.status_code == 201, first.text

    second = await _create(api, api.auth(admin_b, tenant_b), _payload("shared-bot"))

    assert second.status_code == 201, second.text
    assert second.json()["data"]["tenantId"] == str(tenant_b.id)


@pytest.mark.asyncio
async def test_create_with_unknown_knowledge_base_returns_400_and_writes_nothing(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(api, headers, _payload("kb-bot", knowledgeBaseIds=["missing-kb"]))

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_BINDING"
    assert await _list_agent_ids(api, headers) == []
    missing = await api.client.get("/api/agents/kb-bot", headers=headers)
    assert missing.status_code == 404
    # The already-created upstream app is compensated away.
    api.console.delete_app.assert_awaited_once_with("dify-app-1")


@pytest.mark.asyncio
async def test_create_with_unknown_tool_returns_400_and_writes_nothing(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(api, headers, _payload("tool-bot", toolIds=["missing-tool"]))

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_BINDING"
    assert await _list_agent_ids(api, headers) == []
    missing = await api.client.get("/api/agents/tool-bot", headers=headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_create_when_upstream_returns_no_app_id_returns_502_and_writes_nothing(api) -> None:
    api.console.create_app.return_value = {"name": "app-without-id"}
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    resp = await _create(api, headers, _payload("no-id-bot"))

    assert resp.status_code == 502, resp.text
    # C-99 also covers "engine answered without the resource": the envelope is the
    # fixed one — no upstream payload and no `details` may ride along (F1).
    assert resp.json() == {
        "error": {"code": "DIFY_ERROR", "message": "Dify did not return a app id"}
    }
    api.console.create_api_key.assert_not_awaited()
    assert await _list_agent_ids(api, headers) == []


@pytest.mark.asyncio
async def test_create_rolls_back_and_deletes_the_upstream_app_when_a_later_step_fails(
    api, caplog: pytest.LogCaptureFixture
) -> None:
    api.console.create_api_key.side_effect = DifyConsoleError(500, "boom")
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)

    with caplog.at_level(logging.WARNING, logger="app.services.agent_service"):
        resp = await _create(api, headers, _payload("saga-bot"))

    # C-31 compensation is independent of the status mapping: the upstream app
    # created before the failing step must be deleted and no half-written row may
    # survive the failed registration.
    api.console.create_app.assert_awaited_once()
    api.console.delete_app.assert_awaited_once_with("dify-app-1")
    assert await _list_agent_ids(api, headers) == []

    # C-99 pins every inference-engine call failure to 502 `DIFY_ERROR` (C-31
    # repeats that for the create path); the envelope is the fixed one, so the
    # engine's own payload (here `boom`) must not reach the client (F1).
    assert resp.status_code == 502, resp.text
    assert resp.json() == {
        "error": {"code": "DIFY_ERROR", "message": "Inference engine request failed"}
    }
    assert "boom" not in resp.text

    # F1 keeps the engine fault out of the response; this warning is the only
    # server-side record, so it must name the app and upstream status once.
    failed_calls = [
        json.loads(record.getMessage())
        for record in caplog.records
        if "agent_upstream_call_failed" in record.getMessage()
    ]
    assert len(failed_calls) == 1
    assert failed_calls[0]["action"] == "agent_upstream_call_failed"
    assert failed_calls[0]["dify_app_id"] == "dify-app-1"
    assert failed_calls[0]["status_code"] == 500


@pytest.mark.asyncio
async def test_create_binds_assets_and_patch_replaces_the_binding_set_wholesale(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    await api.seed_kb(tenant, kb_id="kb-a")
    await api.seed_kb(tenant, kb_id="kb-b")
    await api.seed_tool(tenant, tool_id="tool-a")
    await api.seed_tool(tenant, tool_id="tool-b")

    created = await _create(
        api,
        headers,
        _payload("bind-bot", knowledgeBaseIds=["kb-a", "kb-b"], toolIds=["tool-a"]),
    )
    assert created.status_code == 201

    detail = await _detail(api, headers, "bind-bot")
    assert sorted(binding["kbId"] for binding in detail["boundKnowledge"]) == ["kb-a", "kb-b"]
    assert [binding["toolId"] for binding in detail["boundTools"]] == ["tool-a"]

    patched = await api.client.patch(
        "/api/agents/bind-bot",
        headers=headers,
        json={"version": 1, "knowledgeBaseIds": ["kb-b"], "toolIds": ["tool-b"]},
    )
    assert patched.status_code == 200

    replaced = await _detail(api, headers, "bind-bot")
    assert [binding["kbId"] for binding in replaced["boundKnowledge"]] == ["kb-b"]
    assert [binding["toolId"] for binding in replaced["boundTools"]] == ["tool-b"]


@pytest.mark.asyncio
async def test_patch_bindings_of_one_tenant_keeps_the_other_tenants_rows(api) -> None:
    # C-102: bindings carry no tenant of their own at the API boundary, so a
    # wholesale replace must scope its delete to the acting tenant. With two
    # tenants sharing the agent identifier, an unscoped delete would also drop
    # the rows tenant B never touched.
    tenant_a, admin_a = await api.seed(role="agent_admin")
    tenant_b, admin_b = await api.seed(role="agent_admin")
    await api.seed_kb(tenant_a, kb_id="kb-a-1")
    await api.seed_kb(tenant_a, kb_id="kb-a-2")
    await api.seed_kb(tenant_b, kb_id="kb-b-1")
    await api.seed_tool(tenant_a, tool_id="tool-a-1")
    await api.seed_tool(tenant_a, tool_id="tool-a-2")
    await api.seed_tool(tenant_b, tool_id="tool-b-1")

    created_a = await _create(
        api,
        api.auth(admin_a, tenant_a),
        _payload("shared-bind-bot", knowledgeBaseIds=["kb-a-1"], toolIds=["tool-a-1"]),
    )
    assert created_a.status_code == 201, created_a.text
    created_b = await _create(
        api,
        api.auth(admin_b, tenant_b),
        _payload("shared-bind-bot", knowledgeBaseIds=["kb-b-1"], toolIds=["tool-b-1"]),
    )
    assert created_b.status_code == 201, created_b.text

    patched = await api.client.patch(
        "/api/agents/shared-bind-bot",
        headers=api.auth(admin_a, tenant_a),
        json={"version": 1, "knowledgeBaseIds": ["kb-a-2"], "toolIds": ["tool-a-2"]},
    )
    assert patched.status_code == 200, patched.text

    replaced = await _detail(api, api.auth(admin_a, tenant_a), "shared-bind-bot")
    assert [(b["kbId"], b["name"]) for b in replaced["boundKnowledge"]] == [("kb-a-2", "kb-a-2")]
    assert [(b["toolId"], b["name"]) for b in replaced["boundTools"]] == [("tool-a-2", "tool-a-2")]

    untouched = await _detail(api, api.auth(admin_b, tenant_b), "shared-bind-bot")
    assert [(b["kbId"], b["name"]) for b in untouched["boundKnowledge"]] == [("kb-b-1", "kb-b-1")]
    assert [(b["toolId"], b["name"]) for b in untouched["boundTools"]] == [("tool-b-1", "tool-b-1")]


@pytest.mark.asyncio
async def test_patch_without_binding_lists_keeps_the_existing_bindings(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    await api.seed_kb(tenant, kb_id="kb-a")
    await api.seed_tool(tenant, tool_id="tool-a")

    created = await _create(
        api, headers, _payload("keep-bind-bot", knowledgeBaseIds=["kb-a"], toolIds=["tool-a"])
    )
    assert created.status_code == 201

    patched = await api.client.patch(
        "/api/agents/keep-bind-bot", headers=headers, json={"version": 1, "name": "Renamed"}
    )
    assert patched.status_code == 200

    detail = await _detail(api, headers, "keep-bind-bot")
    assert detail["name"] == "Renamed"
    assert [binding["kbId"] for binding in detail["boundKnowledge"]] == ["kb-a"]
    assert [binding["toolId"] for binding in detail["boundTools"]] == ["tool-a"]


@pytest.mark.asyncio
async def test_patch_without_version_returns_422(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    await api.seed_agent(tenant, agent_id="bot-1", status="draft")

    resp = await api.client.patch("/api/agents/bot-1", headers=headers, json={"name": "Renamed"})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    unchanged = await _detail(api, headers, "bot-1")
    assert unchanged["name"] == "bot-1"
    assert unchanged["version"] == 1


@pytest.mark.asyncio
async def test_patch_with_stale_version_returns_409(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    await api.seed_agent(tenant, agent_id="bot-1", status="draft")

    resp = await api.client.patch(
        "/api/agents/bot-1", headers=headers, json={"version": 7, "name": "Stale"}
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"
    unchanged = await _detail(api, headers, "bot-1")
    assert unchanged["name"] == "bot-1"
    assert unchanged["version"] == 1


@pytest.mark.asyncio
async def test_patch_with_current_version_succeeds_and_bumps_the_version(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    await api.seed_agent(tenant, agent_id="bot-1", status="draft")

    resp = await api.client.patch(
        "/api/agents/bot-1", headers=headers, json={"version": 1, "name": "Renamed", "tags": ["a"]}
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["name"] == "Renamed"
    assert body["tags"] == ["a"]
    assert body["version"] == 2
    assert (await _detail(api, headers, "bot-1"))["version"] == 2


@pytest.mark.asyncio
async def test_patch_rejects_explicit_null_name_and_tags_with_422(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    await api.seed_agent(tenant, agent_id="bot-1", status="draft")

    for body in ({"version": 1, "name": None}, {"version": 1, "tags": None}):
        resp = await api.client.patch("/api/agents/bot-1", headers=headers, json=body)
        assert resp.status_code == 422, (body, resp.status_code)
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    unchanged = await _detail(api, headers, "bot-1")
    assert unchanged["name"] == "bot-1"
    assert unchanged["tags"] == []


@pytest.mark.asyncio
async def test_delete_returns_204_then_hides_the_agent_from_detail_and_list(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    await api.seed_agent(tenant, agent_id="keep-bot", status="published")
    created = await _create(api, headers, _payload("gone-bot"))
    assert created.status_code == 201

    deleted = await api.client.delete("/api/agents/gone-bot", headers=headers)

    assert deleted.status_code == 204
    assert deleted.content == b""

    detail = await api.client.get("/api/agents/gone-bot", headers=headers)
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "NOT_FOUND"
    assert set(await _list_agent_ids(api, headers)) == {"keep-bot"}


@pytest.mark.asyncio
async def test_delete_returns_204_even_when_upstream_cleanup_fails(api) -> None:
    api.console.delete_app.side_effect = DifyConsoleError(503, "unreachable")
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    created = await _create(api, headers, _payload("gone-bot"))
    assert created.status_code == 201

    deleted = await api.client.delete("/api/agents/gone-bot", headers=headers)

    assert deleted.status_code == 204
    api.console.delete_app.assert_awaited_once_with("dify-app-1")
    detail = await api.client.get("/api/agents/gone-bot", headers=headers)
    assert detail.status_code == 404


@pytest.mark.asyncio
async def test_detail_reports_live_conversations_for_the_delete_confirmation(api) -> None:
    """C-35: admins see the agent's live conversation count, scoped to the agent."""
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    await api.seed_agent(tenant, agent_id="bot-1")
    await api.seed_agent(tenant, agent_id="other-bot")
    await api.seed_agent(tenant, agent_id="empty-bot")
    member = await api.seed_user(tenant)
    async with api.db.session() as session:
        session.add_all(
            [
                Conversation(tenant_id=tenant.id, user_id=member.id, agent_id="bot-1"),
                Conversation(tenant_id=tenant.id, user_id=admin.id, agent_id="bot-1"),
                Conversation(
                    tenant_id=tenant.id,
                    user_id=member.id,
                    agent_id="bot-1",
                    deleted_at=datetime.now(UTC),
                ),
                Conversation(tenant_id=tenant.id, user_id=member.id, agent_id="other-bot"),
            ]
        )
        await session.commit()

    assert (await _detail(api, headers, "bot-1"))["conversationCount"] == 2
    assert (await _detail(api, headers, "empty-bot"))["conversationCount"] == 0


@pytest.mark.asyncio
async def test_detail_hides_the_conversation_count_from_non_admins(api) -> None:
    """C-35/C-53: an employee never sees tenant-wide conversation volume."""
    tenant, _admin = await api.seed(role="agent_admin")
    employee = await api.seed_user(tenant)
    await api.seed_agent(tenant, agent_id="bot-1")
    async with api.db.session() as session:
        session.add(Conversation(tenant_id=tenant.id, user_id=employee.id, agent_id="bot-1"))
        await session.commit()

    detail = await _detail(api, api.auth(employee, tenant), "bot-1")

    assert detail["conversationCount"] == 0


@pytest.mark.asyncio
async def test_delete_keeps_the_agents_history_conversations(api) -> None:
    """C-105: deleting an agent leaves its conversations readable for their owners."""
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    assert (await _create(api, headers, _payload("gone-bot"))).status_code == 201
    async with api.db.session() as session:
        session.add(
            Conversation(
                tenant_id=tenant.id,
                user_id=admin.id,
                agent_id="gone-bot",
                agent_name="Sales Bot",
            )
        )
        await session.commit()

    assert (await api.client.delete("/api/agents/gone-bot", headers=headers)).status_code == 204

    listed = await api.client.get("/api/conversations", headers=headers)
    assert listed.status_code == 200
    assert [(item["agentId"], item["agentName"]) for item in listed.json()["data"]["items"]] == [
        ("gone-bot", "Sales Bot")
    ]


@pytest.mark.asyncio
async def test_agent_writes_are_limited_to_agent_and_platform_admins(api) -> None:
    tenant, _admin = await api.seed(role="agent_admin")
    employee = await api.seed_user(tenant, role="employee")
    await api.seed_agent(tenant, agent_id="bot-1", status="draft")
    headers = api.auth(employee, tenant)

    denied: list[tuple[str, str, dict[str, object]]] = [
        ("POST", "/api/agents", _payload("forbidden-bot")),
        ("PATCH", "/api/agents/bot-1", {"version": 1, "name": "Renamed"}),
        ("DELETE", "/api/agents/bot-1", {}),
        ("POST", "/api/agents/bot-1/publish", {"version": 1}),
        ("POST", "/api/agents/bot-1/start-testing", {"version": 1}),
        ("POST", "/api/agents/bot-1/offline", {}),
    ]
    for method, path, body in denied:
        resp = await api.client.request(
            method, path, headers=headers, json=body if method != "DELETE" else None
        )
        assert resp.status_code == 403, (method, path, resp.status_code)
        assert resp.json()["error"]["code"] == "FORBIDDEN"

    platform_tenant, platform_admin = await api.seed(role="platform_admin")
    allowed = await _create(
        api, api.auth(platform_admin, platform_tenant), _payload("platform-bot")
    )
    assert allowed.status_code == 201


@pytest.mark.asyncio
async def test_agent_endpoints_reject_anonymous_callers_with_401(api) -> None:
    created = await api.client.post("/api/agents", json=_payload("anon-bot"))
    listed = await api.client.get("/api/agents")

    for resp in (created, listed):
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"


# ── the binding facet: narrowed to the agent's tenant (C-102 / C-14 / F1) ──


AGENTS_PATH = "/api/agents"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_PEM = (
    _KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)


class Harness:
    """A running app plus the handles a case needs to steer it."""

    def __init__(self, http: httpx.AsyncClient, console: AsyncMock) -> None:
        self.http = http
        self.console = console

    async def close(self) -> None:
        await self.http.aclose()


def _console_mock() -> AsyncMock:
    """A Dify console double: creation succeeds, teardown is recorded."""
    console = AsyncMock(spec=DifyConsoleClient)
    console.create_app.return_value = {"id": "dify-app-1", "name": "app"}
    console.configure_model.return_value = {"result": "success"}
    console.create_api_key.return_value = {"id": "key-1", "token": "app-secret-1"}
    console.delete_app.return_value = None
    return console


async def _start(session_factory, tenant: Tenant, user: User) -> Harness:  # type: ignore[no-untyped-def]
    """Boot the app against the test database, authenticated as ``user``.

    ``POST /api/agents`` provisions the Dify application before it persists the
    agent, so the route needs ``app.state.dify_console``; the double also records
    the compensation call a rejected registration must still make.
    """
    console = _console_mock()
    app = create_app(Settings(_env_file=None, jwt_public_key=_PUBLIC_PEM, rate_limit_enabled=False))
    app.state.dify_console = console

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    token = pyjwt.encode(
        {"sub": str(user.id), "tenantId": str(tenant.id), "role": user.role},
        _KEY,
        algorithm="RS256",
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    http = httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Harness(http, console)


async def _seed_tenant(session_factory, *, name: str) -> Tenant:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        tenant = Tenant(
            name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}", sso_provider="local"
        )
        session.add(tenant)
        await session.commit()
        return tenant


async def _seed_user(session_factory, tenant: Tenant, *, role: str = "agent_admin"):  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        user = User(
            tenant_id=tenant.id,
            sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
            email=f"{uuid.uuid4().hex[:8]}@{tenant.name.lower()}.test",
            name="Admin",
            role=role,
        )
        session.add(user)
        await session.commit()
        return user


async def _seed_kb(session_factory, tenant: Tenant, *, kb_id: str, name: str):  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        kb = KnowledgeBaseRegistry(
            kb_id=kb_id, tenant_id=tenant.id, dify_dataset_id=f"ds-{kb_id}", name=name
        )
        session.add(kb)
        await session.commit()
        return kb


async def _seed_tool(session_factory, *, tool_id: str, name: str, tenant: Tenant | None):  # type: ignore[no-untyped-def]
    """Seed a tool; ``tenant=None`` is the platform-wide (global) tool of C-65."""
    async with session_factory() as session:
        tool = ToolRegistry(
            tool_id=tool_id, tenant_id=None if tenant is None else tenant.id, name=name
        )
        session.add(tool)
        await session.commit()
        return tool


async def _seed_agent(session_factory, tenant: Tenant, user: User, *, agent_id: str):  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        agent = AgentRegistry(
            agent_id=agent_id,
            tenant_id=tenant.id,
            dify_app_id=f"dify-{agent_id}",
            name=agent_id,
            created_by=user.id,
        )
        session.add(agent)
        await session.commit()
        return agent


async def _bind_row(  # type: ignore[no-untyped-def]
    session_factory,
    tenant: Tenant,
    *,
    agent_id: str,
    kb_id: str | None = None,
    tool_id: str | None = None,
) -> None:
    """Write binding rows directly, reproducing rows a pre-fix deployment stored.

    The agent-identity migration backfilled each binding's tenant from
    ``agent_registry``, so the seed states the agent's tenant itself.
    """
    async with session_factory() as session:
        if kb_id is not None:
            session.add(AgentKnowledgeBinding(tenant_id=tenant.id, agent_id=agent_id, kb_id=kb_id))
        if tool_id is not None:
            session.add(AgentToolBinding(tenant_id=tenant.id, agent_id=agent_id, tool_id=tool_id))
        await session.commit()


async def _count_rows(session_factory, model, *filters) -> int:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        stmt = select(func.count()).select_from(model).where(*filters)
        return int(await session.scalar(stmt) or 0)


async def _harness_detail(harness: Harness, agent_id: str) -> dict[str, object]:
    """Fetch one agent's detail payload, asserting it is readable."""
    response = await harness.http.get(f"{AGENTS_PATH}/{agent_id}")
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


def _error_code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


# ── write side: a foreign asset is refused as unknown (C-102 / C-14) ────────


@pytest.mark.asyncio
async def test_create_rejects_a_foreign_tenant_knowledge_base(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Another tenant's knowledge base cannot be bound and is not echoed back."""
    acme = await _seed_tenant(session_factory, name="Acme")
    globex = await _seed_tenant(session_factory, name="Globex")
    user = await _seed_user(session_factory, acme)
    await _seed_kb(session_factory, globex, kb_id="kb-globex", name="Globex Handbook")

    harness = await _start(session_factory, acme, user)
    try:
        response = await harness.http.post(
            AGENTS_PATH,
            json={"agentId": "bot-fk", "name": "Bot", "knowledgeBaseIds": ["kb-globex"]},
        )
    finally:
        await harness.close()

    assert response.status_code == 400, response.text
    assert _error_code(response) == "INVALID_BINDING"
    assert "kb-globex" not in response.text and "Globex Handbook" not in response.text
    assert await _count_rows(session_factory, AgentRegistry) == 0
    assert await _count_rows(session_factory, AgentKnowledgeBinding) == 0
    # The Dify app created for the request is compensated, as for any rejected
    # registration, so the failure leaves nothing half-written anywhere.
    harness.console.delete_app.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_rejects_a_foreign_tenant_tool(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Another tenant's tool is refused; this tenant's tool list is untouched."""
    acme = await _seed_tenant(session_factory, name="Acme")
    globex = await _seed_tenant(session_factory, name="Globex")
    user = await _seed_user(session_factory, acme)
    await _seed_tool(session_factory, tool_id="tool-globex", name="Globex CRM", tenant=globex)

    harness = await _start(session_factory, acme, user)
    try:
        response = await harness.http.post(
            AGENTS_PATH,
            json={"agentId": "bot-fk", "name": "Bot", "toolIds": ["tool-globex"]},
        )
    finally:
        await harness.close()

    assert response.status_code == 400, response.text
    assert _error_code(response) == "INVALID_BINDING"
    assert "tool-globex" not in response.text and "Globex CRM" not in response.text
    assert await _count_rows(session_factory, AgentRegistry) == 0
    assert await _count_rows(session_factory, AgentToolBinding) == 0


@pytest.mark.asyncio
async def test_create_accepts_own_assets_and_global_tools(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Narrowing stops at the tenant: own knowledge base and global tool bind."""
    acme = await _seed_tenant(session_factory, name="Acme")
    user = await _seed_user(session_factory, acme)
    await _seed_kb(session_factory, acme, kb_id="kb-acme", name="Acme Handbook")
    await _seed_tool(session_factory, tool_id="tool-shared", name="Shared Search", tenant=None)

    harness = await _start(session_factory, acme, user)
    try:
        created = await harness.http.post(
            AGENTS_PATH,
            json={
                "agentId": "bot-own",
                "name": "Bot",
                "knowledgeBaseIds": ["kb-acme"],
                "toolIds": ["tool-shared"],
            },
        )
        assert created.status_code == 201, created.text
        detail = await _harness_detail(harness, "bot-own")
    finally:
        await harness.close()

    assert detail["boundKnowledge"] == [{"kbId": "kb-acme", "name": "Acme Handbook"}]
    assert detail["boundTools"] == [{"toolId": "tool-shared", "name": "Shared Search"}]


@pytest.mark.asyncio
async def test_update_rejects_a_foreign_binding_and_keeps_the_stored_set(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """A refused replacement writes nothing back, not even the version bump."""
    acme = await _seed_tenant(session_factory, name="Acme")
    globex = await _seed_tenant(session_factory, name="Globex")
    user = await _seed_user(session_factory, acme)
    await _seed_kb(session_factory, acme, kb_id="kb-acme", name="Acme Handbook")
    await _seed_tool(session_factory, tool_id="tool-globex", name="Globex CRM", tenant=globex)
    await _seed_agent(session_factory, acme, user, agent_id="bot-replace")
    await _bind_row(session_factory, acme, agent_id="bot-replace", kb_id="kb-acme")

    harness = await _start(session_factory, acme, user)
    try:
        response = await harness.http.patch(
            f"{AGENTS_PATH}/bot-replace",
            json={
                "version": 1,
                "knowledgeBaseIds": [],
                "toolIds": ["tool-globex"],
            },
        )
        assert response.status_code == 400, response.text
        assert _error_code(response) == "INVALID_BINDING"
        detail = await _harness_detail(harness, "bot-replace")
    finally:
        await harness.close()

    assert detail["version"] == 1
    assert detail["boundKnowledge"] == [{"kbId": "kb-acme", "name": "Acme Handbook"}]
    assert detail["boundTools"] == []
    assert await _count_rows(session_factory, AgentToolBinding) == 0


# ── read side: a stored foreign binding is never serialized (F1) ────────────


@pytest.mark.asyncio
async def test_detail_hides_a_binding_that_points_at_another_tenant(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """Rows a pre-fix deployment stored neither surface nor leak field values."""
    acme = await _seed_tenant(session_factory, name="Acme")
    globex = await _seed_tenant(session_factory, name="Globex")
    user = await _seed_user(session_factory, acme)
    await _seed_kb(session_factory, acme, kb_id="kb-acme", name="Acme Handbook")
    await _seed_kb(session_factory, globex, kb_id="kb-globex", name="Globex Handbook")
    await _seed_tool(session_factory, tool_id="tool-globex", name="Globex CRM", tenant=globex)
    await _seed_tool(session_factory, tool_id="tool-shared", name="Shared Search", tenant=None)
    await _seed_agent(session_factory, acme, user, agent_id="bot-legacy")
    await _bind_row(
        session_factory,
        acme,
        agent_id="bot-legacy",
        kb_id="kb-globex",
        tool_id="tool-globex",
    )
    await _bind_row(session_factory, acme, agent_id="bot-legacy", kb_id="kb-acme")
    await _bind_row(session_factory, acme, agent_id="bot-legacy", tool_id="tool-shared")

    harness = await _start(session_factory, acme, user)
    try:
        response = await harness.http.get(f"{AGENTS_PATH}/bot-legacy")
    finally:
        await harness.close()

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["boundKnowledge"] == [{"kbId": "kb-acme", "name": "Acme Handbook"}]
    assert body["boundTools"] == [{"toolId": "tool-shared", "name": "Shared Search"}]
    assert "kb-globex" not in response.text and "Globex" not in response.text
