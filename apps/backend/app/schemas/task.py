"""Pydantic schemas for the task center (architecture doc §32.7.2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApproveTaskDto(BaseModel):
    """Approval payload; ``comment`` is the approver's optional note."""

    comment: str | None = Field(default=None, max_length=500)


class RejectTaskDto(BaseModel):
    """Rejection payload; ``reason`` is required so a rejection is never silent."""

    reason: str = Field(min_length=1, max_length=500)


class TaskRead(BaseModel):
    """Read shape for a task."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    creator_id: uuid.UUID
    assignee_id: uuid.UUID | None
    type: str
    title: str
    priority: str
    status: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error_message: str | None
    retry_count: int
    max_retries: int
    expires_at: datetime | None
    created_at: datetime
    resolved_at: datetime | None


class TaskListRead(BaseModel):
    """Cursor-paginated task list: one page plus the next opaque cursor."""

    items: list[TaskRead]
    next_cursor: str | None
