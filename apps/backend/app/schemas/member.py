"""Member management DTOs (C-28).

The roster exposes the account's provenance and whether a forced password
change is still pending, alongside the profile fields the admin console lists.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.user import User


class MemberDto(BaseModel):
    """One member as the admin console sees it."""

    id: uuid.UUID
    email: str
    name: str
    role: str
    status: str
    department: str | None = None
    avatarText: str | None = None
    lastActiveAt: datetime | None = None
    createdAt: datetime
    accountSource: str
    mustChangePassword: bool


class MemberPageDto(BaseModel):
    """Page-number roster shape: entries plus the tenant's total (C-6)."""

    items: list[MemberDto]
    total: int


class MemberStatusUpdate(BaseModel):
    """A status switch; any other field — a role change included — is refused."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "disabled"]


def member_to_dto(user: User) -> MemberDto:
    """Map a user row onto the roster DTO."""
    return MemberDto(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        status=user.status,
        department=user.department,
        avatarText=user.avatar_text,
        lastActiveAt=user.last_active_at,
        createdAt=user.created_at,
        accountSource=user.account_source,
        mustChangePassword=user.must_change_password,
    )
