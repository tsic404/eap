"""ModelService tests: Dify response normalization and error mapping."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.dify_console import DifyConsoleError
from app.errors import AppError
from app.services.model import (
    ModelService,
    _count_models,
    _localized_label,
    _normalize_providers,
)


def test_localized_label_prefers_zh_then_en() -> None:
    assert _localized_label({"zh_Hans": "OpenAI", "en_US": "OpenAI"}) == "OpenAI"
    assert _localized_label({"en_US": "Anthropic"}) == "Anthropic"
    assert _localized_label("plain") == "plain"
    assert _localized_label(None) is None
    assert _localized_label({}) is None


def test_count_models_ignores_deprecated_and_non_models() -> None:
    config = {
        "models": [
            {"model": "gpt-4o", "deprecated": False},
            {"model": "gpt-3.5", "deprecated": True},
            "not-a-dict",
            {"model": "gpt-4o-mini"},
        ]
    }
    assert _count_models(config) == 2
    assert _count_models(None) == 0
    assert _count_models({"models": "nope"}) == 0


def test_normalize_providers_handles_data_envelope_and_list() -> None:
    raw = {
        "data": [
            {
                "provider": "openai",
                "label": {"zh_Hans": "OpenAI"},
                "preferred_provider_type": "custom",
                "custom_configuration": {"models": [{"model": "gpt-4o"}, {"model": "gpt-4o-mini"}]},
            },
            {
                "provider": "anthropic",
                "label": {"en_US": "Anthropic"},
                "preferred_provider_type": "system",
                "system_configuration": {"models": [{"model": "claude-3"}]},
            },
        ]
    }
    providers = _normalize_providers(raw)

    assert [p.provider for p in providers] == ["openai", "anthropic"]
    assert providers[0].deploymentType == "custom"
    assert providers[0].modelCount == 2
    assert providers[1].deploymentType == "system"
    assert providers[1].modelCount == 1


def test_normalize_providers_skips_malformed_entries() -> None:
    assert _normalize_providers({"unexpected": 1}) == []
    assert _normalize_providers([{"label": "no provider"}]) == []
    assert _normalize_providers(None) == []


@pytest.mark.asyncio
async def test_list_providers_maps_dify_failure_to_502() -> None:
    console = AsyncMock()
    console.get_model_providers = AsyncMock(side_effect=DifyConsoleError(500, "boom"))

    service = ModelService(console)
    with pytest.raises(AppError) as exc:
        await service.list_providers()

    assert exc.value.status_code == 502
    assert exc.value.code == "DIFY_ERROR"


@pytest.mark.asyncio
async def test_get_models_delegates_to_console() -> None:
    console = AsyncMock()
    console.get_models = AsyncMock(return_value={"data": []})

    result = await ModelService(console).get_models("openai")

    assert result == {"data": []}
    console.get_models.assert_awaited_once_with("openai")
