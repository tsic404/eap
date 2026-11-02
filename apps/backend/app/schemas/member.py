"""Member management DTOs (C-28).

The roster exposes the account's provenance and whether a forced password
change is still pending, alongside the profile fields the admin console lists.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.user import User

# The platform's five-role ladder (C-12); a member's role is one of these only.
MemberRole = Literal["platform_admin", "agent_admin", "knowledge_admin", "auditor", "employee"]
MemberStatus = Literal["active", "disabled"]


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


class MemberUpdate(BaseModel):
    """A member adjustment: the role, the status, or both.

    Only the fields being changed travel; a body that names neither changes
    nothing and is refused. A role outside the five-role ladder is refused by
    the field type before the service sees it (C-29).
    """

    model_config = ConfigDict(extra="forbid")

    role: MemberRole | None = None
    status: MemberStatus | None = None

    @model_validator(mode="after")
    def _names_a_change(self) -> MemberUpdate:
        if self.role is None and self.status is None:
            raise ValueError("At least one of role or status is required")
        return self


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
