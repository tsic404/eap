"""Knowledge-base application service: orchestration, validation, Dify, events."""

from __future__ import annotations

import time
from typing import Any

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dify_console import CreateDatasetParams, CreateDocumentParams, DifyConsoleClient
from app.errors import AppError
from app.events.bus import EventBus, bus
from app.events.knowledge import (
    DOCUMENT_INDEX_FAILED_EVENT,
    DOCUMENT_INDEXED_EVENT,
    DOCUMENT_UPLOADED_EVENT,
    KB_CREATED_EVENT,
    KB_DELETED_EVENT,
)
from app.files import validate_document_file
from app.models.knowledge import KnowledgeBaseRegistry
from app.models.tenant import Tenant
from app.repositories.knowledge import KnowledgeRepository
from app.schemas.knowledge import (
    CreateKnowledgeBaseDto,
    DocumentDto,
    DocumentStatusDto,
    KnowledgeBaseDto,
    RetrieveTestCitationDto,
    RetrieveTestDto,
    RetrieveTestResultDto,
)
from app.services.dify_client import DifyClientService

# Dify's default semantic-search indexing; economy (keyword-only) needs no model
# but yields weaker recall. Knowledge recall is the point of this module.
_DEFAULT_INDEXING_TECHNIQUE = "high_quality"


def _document_status(indexing_status: str | None) -> str:
    """Fold Dify's fine-grained indexing statuses into the three exposed here."""
    if indexing_status == "completed":
        return "completed"
    if indexing_status in {"error", "failed"}:
        return "failed"
    return "indexing"


class KnowledgeService:
    """Coordinates the knowledge-base repository, Dify clients, and event bus."""

    def __init__(
        self,
        dify_console: DifyConsoleClient,
        *,
        settings: Settings,
        repository: KnowledgeRepository | None = None,
        event_bus: EventBus | None = None,
        retriever: DifyClientService | None = None,
    ) -> None:
        self._dify_console = dify_console
        self._settings = settings
        self._repository = repository if repository is not None else KnowledgeRepository()
        self._bus = event_bus if event_bus is not None else bus
        # Service-API client used for retrieval testing; built lazily when None.
        self._retriever = retriever

    # ── create / read / delete ──

    async def create(
        self,
        session: AsyncSession,
        tenant: Tenant,
        dto: CreateKnowledgeBaseDto,
    ) -> KnowledgeBaseDto:
        dataset = await self._dify_console.create_dataset(
            CreateDatasetParams(
                name=dto.name,
                description=dto.description,
                indexing_technique=_DEFAULT_INDEXING_TECHNIQUE,
            )
        )
        dify_dataset_id = dataset["id"]
        kb = await self._repository.create(
            session,
            tenant_id=tenant.id,
            dify_dataset_id=dify_dataset_id,
            name=dto.name,
            description=dto.description,
            type=dto.type,
        )
        await session.commit()
        await self._bus.emit(
            KB_CREATED_EVENT,
            kb_id=kb.kb_id,
            tenant_id=tenant.id,
            dify_dataset_id=dify_dataset_id,
        )
        return KnowledgeBaseDto.model_validate(kb)

    async def list_knowledge_bases(
        self, session: AsyncSession, tenant: Tenant, *, offset: int, limit: int
    ) -> list[KnowledgeBaseDto]:
        rows, _ = await self._repository.list_for_tenant(
            session, tenant.id, offset=offset, limit=limit
        )
        return [KnowledgeBaseDto.model_validate(row) for row in rows]

    async def get(self, session: AsyncSession, tenant: Tenant, kb_id: str) -> KnowledgeBaseDto:
        kb = await self._require_kb(session, tenant, kb_id)
        return KnowledgeBaseDto.model_validate(kb)

    async def delete(self, session: AsyncSession, tenant: Tenant, kb_id: str) -> None:
        kb = await self._require_kb(session, tenant, kb_id)
        if kb.agent_bindings:
            raise AppError(
                409,
                "KB_IN_USE",
                "Knowledge base is bound to one or more agents",
            )
        # External side effect first, DB delete second, commit last: a Dify
        # failure leaves the local row untouched (no commit yet).
        await self._dify_console.delete_dataset(kb.dify_dataset_id)
        await self._repository.delete(session, kb)
        await session.commit()
        await self._bus.emit(
            KB_DELETED_EVENT,
            kb_id=kb.kb_id,
            tenant_id=tenant.id,
            dify_dataset_id=kb.dify_dataset_id,
        )

    # ── documents ──

    async def upload_document(
        self,
        session: AsyncSession,
        tenant: Tenant,
        kb_id: str,
        upload: UploadFile,
    ) -> DocumentDto:
        kb = await self._require_kb(session, tenant, kb_id)
        filename = upload.filename or ""
        content = await upload.read()
        validate_document_file(filename, content)

        file_record = await self._dify_console.upload_file(
            filename,
            content,
            mimetype=upload.content_type or "application/octet-stream",
        )
        created = await self._dify_console.create_document(
            kb.dify_dataset_id,
            CreateDocumentParams(name=filename, file_ids=[file_record["id"]]),
        )
        documents = created.get("documents") or []
        if not documents:
            raise AppError(502, "DIFY_UPLOAD_FAILED", "Dify returned no document")
        document = documents[0]

        kb.doc_count += 1
        await session.commit()

        await self._bus.emit(
            DOCUMENT_UPLOADED_EVENT,
            kb_id=kb.kb_id,
            document_id=document["id"],
            document_name=filename,
        )
        return DocumentDto(id=document["id"], name=filename, status="indexing")

    async def list_documents(
        self,
        session: AsyncSession,
        tenant: Tenant,
        kb_id: str,
        *,
        page: int,
        limit: int,
    ) -> list[DocumentDto]:
        kb = await self._require_kb(session, tenant, kb_id)
        result = await self._dify_console.list_documents(
            kb.dify_dataset_id, page=page, limit=limit
        )
        return [
            DocumentDto(
                id=doc["id"],
                name=doc.get("name") or "",
                status=_document_status(doc.get("indexing_status")),
            )
            for doc in result.get("data", [])
        ]

    async def get_document_status(
        self,
        session: AsyncSession,
        tenant: Tenant,
        kb_id: str,
        document_id: str,
    ) -> DocumentStatusDto:
        kb = await self._require_kb(session, tenant, kb_id)
        status = await self._dify_console.get_document_indexing_status(
            kb.dify_dataset_id, document_id
        )
        document_status = _document_status(status.get("indexing_status"))

        if document_status == "completed":
            await self._bus.emit(
                DOCUMENT_INDEXED_EVENT, kb_id=kb.kb_id, document_id=document_id
            )
        elif document_status == "failed":
            await self._bus.emit(
                DOCUMENT_INDEX_FAILED_EVENT,
                kb_id=kb.kb_id,
                document_id=document_id,
                error=status.get("error"),
            )

        return DocumentStatusDto(
            id=document_id,
            status=document_status,
            error=status.get("error"),
        )

    # ── retrieval test ──

    async def retrieval_test(
        self,
        session: AsyncSession,
        tenant: Tenant,
        kb_id: str,
        dto: RetrieveTestDto,
    ) -> RetrieveTestResultDto:
        kb = await self._require_kb(session, tenant, kb_id)

        retriever = self._retriever
        owns_client = retriever is None
        if retriever is None:
            retriever = DifyClientService(
                self._settings.dify_api_base_url, self._settings.dify_api_key
            )
        body: dict[str, Any] = {"query": dto.query}
        if dto.retrieval_model is not None:
            body["retrieval_model"] = dto.retrieval_model
        try:
            started = time.perf_counter()
            result = await retriever.post(
                f"/v1/datasets/{kb.dify_dataset_id}/retrieve", body
            )
            latency_ms = (time.perf_counter() - started) * 1000
        finally:
            if owns_client:
                await retriever.aclose()

        citations = [
            RetrieveTestCitationDto(
                content=_citation_content(record),
                score=float(record.get("score") or 0.0),
                source_name=_citation_source(record),
            )
            for record in result.get("records", [])
        ]
        score = max((c.score for c in citations), default=0.0)
        return RetrieveTestResultDto(
            query=dto.query,
            citations=citations,
            score=score,
            latencyMs=round(latency_ms, 3),
        )

    # ── helpers ──

    async def _require_kb(
        self, session: AsyncSession, tenant: Tenant, kb_id: str
    ) -> KnowledgeBaseRegistry:
        kb = await self._repository.get_for_tenant(session, kb_id, tenant.id)
        if kb is None:
            raise AppError(404, "KB_NOT_FOUND", "Knowledge base not found")
        return kb


def _citation_content(record: dict[str, Any]) -> str:
    segment = record.get("segment") or {}
    return str(segment.get("content") or record.get("content") or "")


def _citation_source(record: dict[str, Any]) -> str | None:
    segment = record.get("segment") or {}
    document = segment.get("document") or {}
    name = document.get("name") or segment.get("document_id")
    return str(name) if name else None
