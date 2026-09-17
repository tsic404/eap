"""Pydantic request/response DTOs for the knowledge-base module (arch doc §32.3.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateKnowledgeBaseDto(BaseModel):
    """Request body for ``POST /api/knowledge-bases``."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    type: str = "business"


class KnowledgeBaseDto(BaseModel):
    """Public knowledge-base view (internal Dify ids are not exposed)."""

    model_config = ConfigDict(from_attributes=True)

    kb_id: str
    name: str
    description: str | None
    type: str
    indexing_status: str | None
    doc_count: int
    chunk_count: int
    created_at: datetime


class DocumentDto(BaseModel):
    """Document created in Dify; ``status`` is ``indexing``/``completed``/``failed``."""

    id: str
    name: str
    status: str


class DocumentStatusDto(BaseModel):
    """Indexing status of a single document (polled from Dify)."""

    id: str
    status: str
    error: str | None = None


class RetrieveTestDto(BaseModel):
    """Request body for ``POST /api/knowledge-bases/:id/retrieval-test``."""

    query: str = Field(min_length=1)
    retrieval_model: dict[str, Any] | None = None


class RetrieveTestCitationDto(BaseModel):
    """A single recalled chunk from Dify's retrieve response."""

    content: str
    score: float
    source_name: str | None = None


class RetrieveTestResultDto(BaseModel):
    """Retrieval test result: citations plus the top score and round-trip latency."""

    query: str
    citations: list[RetrieveTestCitationDto]
    score: float
    latencyMs: float
