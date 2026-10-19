"""Model application service: proxy Dify Console model-provider queries (§15.2).

Dify model providers are workspace-scoped (a single console workspace), so this
service carries no tenant dimension; the controller gates access to admin roles
instead. Responses are normalized into a stable public shape so the frontend
never depends on Dify's raw payload layout.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.dify_console import DifyConsoleClient, DifyConsoleError
from app.errors import AppError
from app.schemas.model import ModelProviderDto

# Locale keys tried in order when localizing Dify's ``label`` map.
_LABEL_LOCALES = ("zh_Hans", "en_US", "zh-Hans", "en")


def _non_empty_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _localized_label(label: Any) -> str | None:
    """Extract a human-facing name from Dify's locale-keyed ``label`` field."""
    if isinstance(label, str):
        return label or None
    if isinstance(label, dict):
        for locale in _LABEL_LOCALES:
            value = _non_empty_string(label.get(locale))
            if value is not None:
                return value
    return None


def _count_models(configuration: Any) -> int:
    """Count the models Dify lists inside one provider configuration.

    Dify 1.17.0's console provider list builds ``custom_models`` from the
    provider's custom-model rows, so that key always carries a list — empty
    when the provider exposes none; a missing key, ``null`` or any other type
    counts zero. Only custom configurations are counted: the system
    configuration block is not a model list.
    """
    if not isinstance(configuration, dict):
        return 0
    models = configuration.get("custom_models")
    if not isinstance(models, list):
        return 0
    return sum(1 for model in models if isinstance(model, dict) and not model.get("deprecated"))


def _provider_entries(raw: Any) -> list[Any]:
    """Unwrap the two accepted payload shapes; anything else is a failed call."""
    if isinstance(raw, dict):
        entries = raw.get("data")
        if isinstance(entries, list):
            return entries
    if isinstance(raw, list):
        return raw
    raise AppError(502, "DIFY_ERROR", "Unrecognizable model provider payload")


def _provider_identifier(entry: dict[str, Any]) -> str | None:
    for field in ("provider", "key"):
        value = _non_empty_string(entry.get(field))
        if value is not None:
            return value
    return None


def _normalize_providers(raw: Any) -> list[ModelProviderDto]:
    """Map Dify's ``ProviderListResponse`` onto ``ModelProviderDto`` items."""
    providers: list[ModelProviderDto] = []
    for entry in _provider_entries(raw):
        if not isinstance(entry, dict):
            continue
        provider = _provider_identifier(entry)
        if provider is None:
            continue
        deployment_type = entry.get("preferred_provider_type")
        providers.append(
            ModelProviderDto(
                provider=provider,
                label=_localized_label(entry.get("label")) or provider,
                deploymentType=deployment_type if isinstance(deployment_type, str) else None,
                modelCount=_count_models(entry.get("custom_configuration")),
            )
        )
    return providers


class ModelService:
    """Read-only proxy over ``DifyConsoleClient`` model-provider endpoints."""

    def __init__(self, console: DifyConsoleClient) -> None:
        self._console = console

    async def list_providers(self) -> list[ModelProviderDto]:
        """Return provider summaries (provider + deployment type + model count)."""
        try:
            raw = await self._console.get_model_providers()
        except (DifyConsoleError, httpx.HTTPError) as exc:
            raise AppError(502, "DIFY_ERROR", "Failed to load model providers") from exc
        return _normalize_providers(raw)

    async def get_models(self, provider: str) -> Any:
        """Proxy Dify's per-provider model list (backs future detail views)."""
        return await self._console.get_models(provider)
