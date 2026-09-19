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


def _localized_label(label: Any) -> str | None:
    """Extract a human-facing name from Dify's locale-keyed ``label`` field."""
    if isinstance(label, str):
        return label or None
    if isinstance(label, dict):
        for locale in _LABEL_LOCALES:
            value = label.get(locale)
            if value:
                return str(value)
        for value in label.values():
            if value:
                return str(value)
    return None


def _count_models(configuration: Any) -> int:
    """Count non-deprecated models inside one provider configuration."""
    if not isinstance(configuration, dict):
        return 0
    models = configuration.get("models")
    if not isinstance(models, list):
        return 0
    return sum(1 for model in models if isinstance(model, dict) and not model.get("deprecated"))


def _normalize_providers(raw: Any) -> list[ModelProviderDto]:
    """Map Dify's ``ProviderListResponse`` onto ``ModelProviderDto`` items."""
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        entries = raw["data"]
    elif isinstance(raw, list):
        entries = raw
    else:
        return []

    providers: list[ModelProviderDto] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        provider = entry.get("provider") or entry.get("key")
        if not provider:
            continue
        providers.append(
            ModelProviderDto(
                provider=str(provider),
                label=_localized_label(entry.get("label")) or str(provider),
                deploymentType=entry.get("preferred_provider_type"),
                modelCount=_count_models(entry.get("custom_configuration"))
                + _count_models(entry.get("system_configuration")),
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
