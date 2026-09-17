"""Auth API response models."""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class CurrentUser(BaseModel):
    """The authenticated user returned by ``GET /api/me``."""

    id: uuid.UUID
    tenantId: uuid.UUID
    email: str
    name: str
    role: str
    department: str | None = None
    avatarText: str | None = None
