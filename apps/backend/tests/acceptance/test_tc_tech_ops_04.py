"""Acceptance case for the model-provider listing contract.

The case drives ``GET /api/models`` end to end: the Dify Console boundary is the
only mocked hop (``api.console.get_model_providers``), while the role ladder,
payload normalization and error mapping are the real ones. Every test seeds its
own tenant, so no clause depends on another test's leftovers.
"""

import httpx
import pytest

from app.dify_console import DifyConsoleError

TC_ID = "TC-tech-ops-04"

_PROVIDER_FIELDS = {"provider", "label", "deploymentType", "modelCount"}


async def _list(api, headers: dict[str, str]):
    return await api.client.get("/api/models", headers=headers)


async def _providers(api, headers: dict[str, str], payload: object) -> list[dict]:
    api.console.get_model_providers.return_value = payload
    resp = await _list(api, headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _entry(provider: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {"provider": provider}
    body.update(overrides)
    return body


# ── C-64: role ladder ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_platform_and_agent_admins_may_list_model_providers(api) -> None:
    tenant, admin = await api.seed(role="platform_admin")
    api.console.get_model_providers.return_value = {"data": []}

    for role in ("platform_admin", "agent_admin"):
        user = admin if role == "platform_admin" else await api.seed_user(tenant, role=role)
        assert (await _list(api, api.auth(user, tenant))).status_code == 200, role

    for role in ("employee", "knowledge_admin", "auditor"):
        user = await api.seed_user(tenant, role=role)
        denied = await _list(api, api.auth(user, tenant))
        assert denied.status_code == 403, role
        assert denied.json()["error"]["code"] == "FORBIDDEN"

    anonymous = await _list(api, {})
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "UNAUTHORIZED"


# ── C-20: response shape ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_response_is_an_array_and_every_item_carries_exactly_four_fields(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    providers = await _providers(
        api,
        api.auth(admin, tenant),
        {
            "data": [
                _entry(
                    "openai",
                    label={"zh_Hans": "OpenAI"},
                    preferred_provider_type="custom",
                    custom_configuration={"custom_models": [{"model": "gpt-4o"}]},
                ),
                _entry("anthropic", label="Anthropic", preferred_provider_type="system"),
            ]
        },
    )

    assert isinstance(providers, list)
    assert len(providers) == 2
    for item in providers:
        assert set(item) == _PROVIDER_FIELDS
        assert isinstance(item["provider"], str)


# ── C-20: identifier, display name and deployment type ──────────────────────


@pytest.mark.asyncio
async def test_identifier_falls_back_to_the_key_and_entries_without_one_are_skipped(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    providers = await _providers(
        api,
        api.auth(admin, tenant),
        [
            _entry("openai"),
            {"provider": "", "key": "fallback-key"},
            {"key": "key-only"},
            {"provider": None, "key": "none-key"},
            {"label": {"zh_Hans": "没有标识"}},
            {"provider": ""},
        ],
    )

    assert [item["provider"] for item in providers] == [
        "openai",
        "fallback-key",
        "key-only",
        "none-key",
    ]


@pytest.mark.xfail(
    reason=(
        "C-20 provider identifier must be the entry's non-empty string (a non-string value "
        "falls back to the alternative key, and without one the entry is skipped); "
        "main@68ad4b5 stringifies any truthy value instead"
    ),
    strict=False,
)
@pytest.mark.asyncio
async def test_non_string_identifier_falls_back_to_the_key_or_is_skipped(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    providers = await _providers(
        api,
        api.auth(admin, tenant),
        [
            {"provider": 123, "key": "numeric-key"},
            {"provider": ["openai"], "key": "list-key"},
            {"provider": 456},
            _entry("ok"),
        ],
    )

    assert [item["provider"] for item in providers] == ["numeric-key", "list-key", "ok"]


@pytest.mark.asyncio
async def test_display_name_prefers_chinese_then_english_and_falls_back_to_the_identifier(
    api,
) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    providers = await _providers(
        api,
        api.auth(admin, tenant),
        [
            _entry("a", label={"zh_Hans": "中文名", "en_US": "English name"}),
            _entry("b", label={"en_US": "English name"}),
            _entry("c", label={"zh_Hans": "", "en_US": "English name"}),
            _entry("d", label={"zh_Hans": ""}),
            _entry("e", label={}),
            _entry("f"),
            _entry("g", label="Plain name"),
        ],
    )

    assert {item["provider"]: item["label"] for item in providers} == {
        "a": "中文名",
        "b": "English name",
        "c": "English name",
        "d": "d",
        "e": "e",
        "f": "f",
        "g": "Plain name",
    }
    assert all(item["label"] for item in providers)


@pytest.mark.xfail(
    reason=(
        "C-20 display name must only consider non-empty string values inside the locale map "
        "(Chinese before English); main@68ad4b5 stringifies whatever value it finds"
    ),
    strict=False,
)
@pytest.mark.asyncio
async def test_display_name_skips_non_string_locale_values(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    providers = await _providers(
        api,
        api.auth(admin, tenant),
        [
            _entry("n1", label={"zh_Hans": 123, "en_US": "English"}),
            _entry("n2", label={"zh_Hans": ["x"], "en_US": "English"}),
            _entry("n3", label={"zh_Hans": 123}),
        ],
    )

    assert {item["provider"]: item["label"] for item in providers} == {
        "n1": "English",
        "n2": "English",
        "n3": "n3",
    }


@pytest.mark.xfail(
    reason=(
        "C-20 display name falls back to the identifier when no Chinese or English value is "
        "available; main@68ad4b5 falls back to any other locale first"
    ),
    strict=False,
)
@pytest.mark.asyncio
async def test_display_name_ignores_locales_outside_chinese_and_english(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    providers = await _providers(
        api,
        api.auth(admin, tenant),
        [
            _entry("fr", label={"fr_FR": "Nom"}),
            _entry("ja", label={"ja_JP": "名前", "zh_Hans": "", "en_US": ""}),
        ],
    )

    assert {item["provider"]: item["label"] for item in providers} == {"fr": "fr", "ja": "ja"}


@pytest.mark.asyncio
async def test_deployment_type_passes_through_and_is_the_present_null_field(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    providers = await _providers(
        api,
        api.auth(admin, tenant),
        [
            _entry("t1", preferred_provider_type="system"),
            _entry("t2"),
        ],
    )

    assert {item["provider"]: item["deploymentType"] for item in providers} == {
        "t1": "system",
        "t2": None,
    }
    assert all("deploymentType" in item for item in providers)


@pytest.mark.xfail(
    reason="C-20 non-string deployment type is not nulled at main@68ad4b5", strict=False
)
@pytest.mark.asyncio
async def test_non_string_deployment_type_becomes_null_instead_of_an_error(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    providers = await _providers(
        api, api.auth(admin, tenant), [_entry("t3", preferred_provider_type=123)]
    )

    assert providers[0]["deploymentType"] is None


# ── C-20: model count ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_count_counts_listed_non_deprecated_object_entries(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    providers = await _providers(
        api,
        api.auth(admin, tenant),
        [
            _entry(
                "m1",
                custom_configuration={
                    "status": "active",
                    "custom_models": [
                        {"model": "a"},
                        {"model": "b"},
                        {"model": "c", "deprecated": True},
                        "not-a-dict",
                        42,
                        None,
                    ],
                },
            ),
            # No credentials configured yet: the count is unaffected (C-20).
            _entry(
                "m2",
                custom_configuration={"status": "no-configure", "custom_models": [{"model": "x"}]},
            ),
        ],
    )

    assert {item["provider"]: item["modelCount"] for item in providers} == {"m1": 2, "m2": 1}


@pytest.mark.asyncio
async def test_model_count_is_zero_for_missing_unnumberable_or_malformed_configuration(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    providers = await _providers(
        api,
        api.auth(admin, tenant),
        [
            _entry("z1"),
            _entry("z2", custom_configuration=None),
            _entry("z3", custom_configuration="nope"),
            _entry("z4", custom_configuration={"custom_models": None}),
            _entry("z5", custom_configuration={"custom_models": []}),
            _entry(
                "z6",
                custom_configuration={"custom_models": [{"model": "a", "deprecated": True}]},
            ),
        ],
    )

    assert [item["modelCount"] for item in providers] == [0, 0, 0, 0, 0, 0]
    assert all(isinstance(item["modelCount"], int) for item in providers)


@pytest.mark.xfail(
    reason="C-20 system configuration block still counts at main@68ad4b5", strict=False
)
@pytest.mark.asyncio
async def test_system_configuration_block_does_not_contribute_to_the_model_count(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    providers = await _providers(
        api,
        api.auth(admin, tenant),
        [
            _entry(
                "sys",
                preferred_provider_type="custom",
                custom_configuration={"custom_models": [{"model": "a"}, {"model": "b"}]},
                system_configuration={
                    "enabled": True,
                    "custom_models": [{"model": "x"}, {"model": "y"}, {"model": "z"}],
                },
            )
        ],
    )

    assert providers[0]["modelCount"] == 2


# ── C-20: payload forms and failure mapping ─────────────────────────────────


@pytest.mark.asyncio
async def test_wrapped_and_bare_array_payloads_are_equivalent(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)
    entry = _entry(
        "eq",
        label={"en_US": "Eq"},
        preferred_provider_type="custom",
        custom_configuration={"custom_models": [{"model": "a"}]},
    )

    wrapped = await _providers(api, headers, {"data": [entry]})
    bare = await _providers(api, headers, [entry])

    assert (
        wrapped
        == bare
        == [{"provider": "eq", "label": "Eq", "deploymentType": "custom", "modelCount": 1}]
    )


@pytest.mark.asyncio
async def test_empty_provider_list_is_a_plain_empty_array(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)

    assert await _providers(api, headers, []) == []
    assert await _providers(api, headers, {"data": []}) == []


@pytest.mark.xfail(
    reason="C-20 unrecognizable payload is not reported at main@68ad4b5", strict=False
)
@pytest.mark.asyncio
async def test_unrecognizable_upstream_payload_returns_502_not_an_empty_array(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    headers = api.auth(admin, tenant)

    for payload in ({"unexpected": 1}, {"data": "nope"}, None):
        api.console.get_model_providers.return_value = payload
        resp = await _list(api, headers)
        assert resp.status_code == 502, payload
        assert resp.json()["error"]["code"] == "DIFY_ERROR"


@pytest.mark.asyncio
async def test_upstream_failure_returns_502_dify_error(api) -> None:
    tenant, admin = await api.seed(role="agent_admin")
    api.console.get_model_providers.side_effect = [
        DifyConsoleError(500, "boom"),
        httpx.ConnectError("console unreachable"),
    ]
    headers = api.auth(admin, tenant)

    for _ in range(2):
        resp = await _list(api, headers)
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "DIFY_ERROR"
