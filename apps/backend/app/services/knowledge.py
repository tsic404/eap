"""Knowledge-base application service: orchestration, validation, Dify, events."""

from __future__ import annotations

import time
from typing import Any

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dify_console import (
    CreateDatasetParams,
    CreateDocumentParams,
    DifyConsoleClient,
    DifyConsoleError,
)
from app.errors import AppError
from app.events.bus import EventBus, bus
from app.events.knowledge import (
    DOCUMENT_INDEX_FAILED_EVENT,
    DOCUMENT_INDEXED_EVENT,
    DOCUMENT_UPLOADED_EVENT,
    KB_CREATED_EVENT,
    KB_DELETED_EVENT,
)
from app.files import MAX_FILE_SIZE_BYTES, validate_document_file
from app.logging_conf import get_logger
from app.models.knowledge import KnowledgeBaseRegistry
from app.models.tenant import Tenant
from app.repositories.knowledge import KnowledgeRepository
from app.schemas.knowledge import (
    CreateKnowledgeBaseDto,
    DocumentDto,
    DocumentStatus,
    DocumentStatusDto,
    KnowledgeBaseDto,
    KnowledgeBasePageDto,
    RetrieveTestCitationDto,
    RetrieveTestDto,
    RetrieveTestResultDto,
)
from app.services.dify_client import DifyClientService

log = get_logger(__name__)

# Dify's default semantic-search indexing; economy (keyword-only) needs no model
# but yields weaker recall. Knowledge recall is the point of this module.
_DEFAULT_INDEXING_TECHNIQUE = "high_quality"

_TERMINAL_STATUSES = frozenset({"completed", "failed"})


def _document_status(indexing_status: str | None) -> DocumentStatus:
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
        # Service-API client used for retrieval testing; built per-KB when None.
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
        try:
            api_key = await self._get_or_create_dataset_api_key()
            kb = await self._repository.create(
                session,
                tenant_id=tenant.id,
                dify_dataset_id=dify_dataset_id,
                dify_api_key=api_key,
                name=dto.name,
                description=dto.description,
                type=dto.type,
            )
            await session.commit()
        except Exception:
            # Compensate: a half-created Dify dataset must not leak as an orphan.
            try:
                await self._dify_console.delete_dataset(dify_dataset_id)
            except Exception:
                log.warning(
                    "knowledge_base_create_compensation_failed",
                    dify_dataset_id=dify_dataset_id,
                    exc_info=True,
                )
            raise
        await self._bus.emit(
            KB_CREATED_EVENT,
            kb_id=kb.kb_id,
            tenant_id=tenant.id,
            dify_dataset_id=dify_dataset_id,
        )
        return KnowledgeBaseDto.model_validate(kb)

    async def list_knowledge_bases(
        self, session: AsyncSession, tenant: Tenant, *, offset: int, limit: int
    ) -> KnowledgeBasePageDto:
        rows, total = await self._repository.list_for_tenant(
            session, tenant.id, offset=offset, limit=limit
        )
        return KnowledgeBasePageDto(
            items=[KnowledgeBaseDto.model_validate(row) for row in rows],
            total=total,
        )

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
        try:
            await self._dify_console.delete_dataset(kb.dify_dataset_id)
        except DifyConsoleError as exc:
            if exc.status_code != 404:
                raise
            # Dataset already gone (e.g. retry after a partial delete) — the
            # local row still needs cleaning, so fall through.
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
        # Pre-check Content-Length so an oversized upload is rejected before it
        # is buffered into memory; ``validate_document_file`` re-checks the
        # actual byte count as the authoritative guard.
        if upload.size is not None and upload.size > MAX_FILE_SIZE_BYTES:
            raise AppError(422, "FILE_TOO_LARGE", "File exceeds the 15 MB limit")
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
        await self._repository.insert_document(
            session,
            kb_id=kb.kb_id,
            document_id=document["id"],
            name=filename,
        )
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
        new_status = _document_status(status.get("indexing_status"))
        error = status.get("error")

        # Atomic conditional update: the row only changes on a genuine
        # transition, so concurrent polls cannot double-emit. Commit first so
        # the event only fires once the state change is durable.
        transitioned = False
        if new_status in _TERMINAL_STATUSES:
            transitioned = await self._repository.transition_document_status(
                session,
                kb_id=kb.kb_id,
                document_id=document_id,
                status=new_status,
                error=error,
            )
            await session.commit()

        if transitioned:
            if new_status == "completed":
                await self._bus.emit(
                    DOCUMENT_INDEXED_EVENT, kb_id=kb.kb_id, document_id=document_id
                )
            else:
                await self._bus.emit(
                    DOCUMENT_INDEX_FAILED_EVENT,
                    kb_id=kb.kb_id,
                    document_id=document_id,
                    error=error,
                )

        return DocumentStatusDto(id=document_id, status=new_status, error=error)

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
            if not kb.dify_api_key:
                raise AppError(
                    409, "KB_API_KEY_MISSING", "Knowledge base has no dataset API key"
                )
            # Dataset-scoped bearer: a per-KB key, never the global app key.
            retriever = DifyClientService(self._settings.dify_api_base_url, kb.dify_api_key)
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

    async def _get_or_create_dataset_api_key(self) -> str:
        """Reuse the tenant's existing dataset API key, creating one only if absent.

        Dify 1.17.0 caps dataset keys at 10 per tenant, so a key per knowledge
        base would exhaust the pool; all KBs in a tenant share one key instead.
        """
        keys = await self._dify_console.get_dataset_api_keys()
        existing = keys.get("data") or []
        if existing:
            return str(existing[0]["token"])
        created = await self._dify_console.create_dataset_api_key()
        return str(created["token"])

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
