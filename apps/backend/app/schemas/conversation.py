"""Conversation module request/response DTOs (architecture doc §32.4.2).

Request/response field names are camelCase to match the platform's public API
contract (the agent module DTOs and the frontend types use the same shape).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ConversationStatus(StrEnum):
    active = "active"
    archived = "archived"


class CreateConversationDto(BaseModel):
    """Body of ``POST /api/conversations``."""

    agentId: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)


class MessageFileDto(BaseModel):
    """A message attachment; ``id`` is the Dify upload-file id.

    ``id`` is required so a malformed entry is rejected as a 422 *before* the
    SSE response starts, rather than surfacing as a mid-stream ``KeyError``.
    """

    id: str = Field(min_length=1)
    type: Literal["image", "document", "audio", "video", "custom"] = "document"


class SendMessageDto(BaseModel):
    """Body of ``POST /api/conversations/{id}/messages`` (SSE streaming)."""

    query: str = Field(min_length=1, max_length=10000)
    agentId: str = Field(min_length=1)
    files: list[MessageFileDto] | None = Field(default=None, max_length=5)


class ListConversationsDto(BaseModel):
    """Query parameters of ``GET /api/conversations`` (keyset cursor)."""

    agentId: str | None = None
    status: ConversationStatus = ConversationStatus.active
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None


class ConversationDto(BaseModel):
    """Public conversation view (Dify's internal conversation id is not exposed)."""

    id: str
    title: str | None
    agentId: str
    agentName: str | None
    status: str
    createdAt: datetime
    updatedAt: datetime


class ConversationPageDto(BaseModel):
    """Cursor-paginated conversation listing."""

    items: list[ConversationDto]
    nextCursor: str | None


class MessageAttachmentDto(BaseModel):
    """A file attached to a history message (Dify ``message_files`` entry)."""

    id: str
    type: str
    url: str | None = None
    belongsTo: Literal["user", "assistant"] | None = None


class MessageDto(BaseModel):
    """One history message: a user query paired with the assistant's answer.

    ``feedback`` is Dify's ``like``/``dislike`` rating, or ``None`` when the
    user has not rated this message. ``createdAt`` is ``None`` when Dify did not
    report a valid timestamp for the message.
    """

    id: str
    query: str
    answer: str
    status: str
    feedback: Literal["like", "dislike"] | None = None
    files: list[MessageAttachmentDto] = Field(default_factory=list)
    createdAt: datetime | None = None


class MessagePageDto(BaseModel):
    """Cursor-paginated message history, newest first.

    ``nextCursor`` is the opaque cursor for the next (older) page: the id of the
    oldest message in this page, or ``None`` when there is no older history.
    """

    items: list[MessageDto]
    nextCursor: str | None = None
