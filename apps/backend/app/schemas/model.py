"""Model module response DTOs (architecture doc §15.2).

Field names are camelCase to match the platform's public API contract.
"""

from __future__ import annotations

from pydantic import BaseModel


class ModelProviderDto(BaseModel):
    """One Dify model provider summary shown by ``GET /api/models``.

    ``provider`` is Dify's provider key (e.g. ``openai``); ``label`` is the
    human-facing name when Dify supplies one; ``deploymentType`` is Dify's
    ``preferred_provider_type`` (``custom``/``system``/``model``); ``modelCount``
    is the number of non-deprecated models Dify currently exposes for it.
    """

    provider: str
    label: str | None = None
    deploymentType: str | None = None
    modelCount: int
