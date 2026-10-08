"""Knowledge-base application service: orchestration, validation, Dify, events."""

from __future__ import annotations

import json
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
    DocumentPageDto,
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


def _segment_count(status: dict[str, Any]) -> int:
    """Segments Dify holds for a document; 0 when the payload omits the field.

    ``total_segments`` is the document's live ``document_segments`` row count —
    the quantity the KB card's chunk count reports.
    """
    return int(status.get("total_segments") or 0)


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
        # Reject a duplicate name before any Dify dataset exists, so the
        # client gets a displayable 409 and nothing needs compensation.
        existing = await self._repository.get_by_name_for_tenant(session, tenant.id, dto.name)
        if existing is not None:
            raise AppError(409, "KB_NAME_EXISTS", "知识库名称已存在")
        try:
            dataset = await self._dify_console.create_dataset(
                CreateDatasetParams(
                    name=dto.name,
                    description=dto.description,
                    indexing_technique=_DEFAULT_INDEXING_TECHNIQUE,
                )
            )
        except DifyConsoleError as exc:
            # A concurrent duplicate can slip past the pre-check; Dify answers
            # 409 and it must map to the same conflict, not bubble up as a 500.
            if exc.status_code == 409:
                raise AppError(409, "KB_NAME_EXISTS", "知识库名称已存在") from exc
            raise
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

        await self._repository.add_document_count(session, kb_id=kb.kb_id)
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
    ) -> DocumentPageDto:
        kb = await self._require_kb(session, tenant, kb_id)
        result = await self._dify_console.list_documents(kb.dify_dataset_id, page=page, limit=limit)
        observed = [
            (
                DocumentDto(
                    id=doc["id"],
                    name=doc.get("name") or "",
                    status=_document_status(doc.get("indexing_status")),
                ),
                doc.get("error"),
            )
            for doc in result.get("data", [])
        ]
        # The UI polls this listing while documents index and never calls the
        # per-document status endpoint, so a terminal status is usually first
        # observed here: record it, or the completion state machine (and the
        # KB's chunk total) stays frozen at ``indexing`` in the product flow.
        await self._record_listed_terminals(session, kb, observed)
        return DocumentPageDto(
            items=[item for item, _ in observed],
            total=int(result.get("total") or 0),
        )

    async def get_document_status(
        self,
        session: AsyncSession,
        tenant: Tenant,
        kb_id: str,
        document_id: str,
    ) -> DocumentStatusDto:
        kb = await self._require_kb(session, tenant, kb_id)
        _require_document_id(document_id)
        status = await self._dify_console.get_document_indexing_status(
            kb.dify_dataset_id, document_id
        )
        new_status = _document_status(status.get("indexing_status"))
        error = status.get("error")

        # Commit first so the event only fires once the state change is durable.
        transitioned = False
        if new_status in _TERMINAL_STATUSES:
            transitioned = await self._record_terminal_status(
                session,
                kb,
                document_id=document_id,
                status=new_status,
                error=error,
                segments=_segment_count(status) if new_status == "completed" else None,
            )
            await session.commit()

        if transitioned:
            await self._emit_terminal_event(kb.kb_id, document_id, new_status, error)

        return DocumentStatusDto(id=document_id, status=new_status, error=error)

    async def retry_document(
        self,
        session: AsyncSession,
        tenant: Tenant,
        kb_id: str,
        document_id: str,
    ) -> None:
        """Restart indexing for a failed document; Dify owns the resulting state."""
        kb = await self._require_kb(session, tenant, kb_id)
        _require_document_id(document_id)
        try:
            await self._dify_console.retry_document_indexing(kb.dify_dataset_id, [document_id])
        except DifyConsoleError as exc:
            raise _document_operation_error(exc, code="DOCUMENT_RETRY_FAILED") from exc

    async def delete_document(
        self,
        session: AsyncSession,
        tenant: Tenant,
        kb_id: str,
        document_id: str,
    ) -> None:
        kb = await self._require_kb(session, tenant, kb_id)
        _require_document_id(document_id)
        # Eligibility mirrors the accumulator: segments were added only when a
        # recorded transition into ``completed`` succeeded, so the local row —
        # not Dify's live status — decides whether anything is owed back. A
        # document that finished in Dify but was never observed locally stays
        # out of the subtraction (its segments were never added).
        tracked = await self._repository.get_document(
            session, kb_id=kb.kb_id, document_id=document_id
        )
        counted = tracked is not None and tracked.status == "completed"
        try:
            # The amount still comes from Dify, read while it holds the document.
            segments = 0
            if counted:
                status = await self._dify_console.get_document_indexing_status(
                    kb.dify_dataset_id, document_id
                )
                segments = _segment_count(status)
            await self._dify_console.delete_document(kb.dify_dataset_id, document_id)
        except DifyConsoleError as exc:
            raise _document_operation_error(exc, code="DOCUMENT_DELETE_FAILED") from exc
        # Dify dropped the document; retire the local row and its share of both
        # KB totals, so the card stops counting a document that is gone.
        if await self._repository.delete_document(session, kb_id=kb.kb_id, document_id=document_id):
            await self._repository.release_document_counts(
                session, kb_id=kb.kb_id, segments=segments
            )
        await session.commit()

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
                raise AppError(409, "KB_API_KEY_MISSING", "Knowledge base has no dataset API key")
            # Dataset-scoped bearer: a per-KB key, never the global app key.
            retriever = DifyClientService(self._settings.dify_api_base_url, kb.dify_api_key)
        body: dict[str, Any] = {"query": dto.query}
        if dto.retrieval_model is not None:
            body["retrieval_model"] = dto.retrieval_model
        try:
            started = time.perf_counter()
            result = await retriever.post(f"/v1/datasets/{kb.dify_dataset_id}/retrieve", body)
            latency_ms = (time.perf_counter() - started) * 1000
        finally:
            if owns_client:
                await retriever.aclose()

        citations = [
            RetrieveTestCitationDto(
                content=_citation_content(record),
                score=float(record.get("score") or 0.0),
                source_name=_citation_source(record),
                kb_name=_citation_kb_name(record, kb.name),
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

    async def _record_listed_terminals(
        self,
        session: AsyncSession,
        kb: KnowledgeBaseRegistry,
        observed: list[tuple[DocumentDto, str | None]],
    ) -> None:
        """Persist the terminal states a document listing reported.

        Transition and count share one transaction, so a document is never
        recorded as completed without its segments; the guarded update inside
        ``_record_terminal_status`` keeps concurrent listings from double-counting.
        """
        recorded: list[tuple[DocumentDto, str | None]] = []
        for item, error in observed:
            if item.status not in _TERMINAL_STATUSES:
                continue
            if await self._record_terminal_status(
                session, kb, document_id=item.id, status=item.status, error=error
            ):
                recorded.append((item, error))
        if not recorded:
            return
        await session.commit()
        for item, error in recorded:
            await self._emit_terminal_event(kb.kb_id, item.id, item.status, error)

    async def _record_terminal_status(
        self,
        session: AsyncSession,
        kb: KnowledgeBaseRegistry,
        *,
        document_id: str,
        status: DocumentStatus,
        error: str | None,
        segments: int | None = None,
    ) -> bool:
        """Persist one terminal document status; ``True`` only on a real transition.

        The conditional update runs first, so a document whose stored status
        already matches costs no upstream call — its segment count is fetched
        from Dify only when the transition is genuine (``segments`` lets a caller
        that already holds the indexing-status payload pass it in).
        """
        transitioned = await self._repository.transition_document_status(
            session, kb_id=kb.kb_id, document_id=document_id, status=status, error=error
        )
        if not transitioned:
            return False
        if status == "completed":
            if segments is None:
                payload = await self._dify_console.get_document_indexing_status(
                    kb.dify_dataset_id, document_id
                )
                segments = _segment_count(payload)
            # Each document contributes its own segments to the KB-wide total;
            # only a status change adds them, so repeat polls and re-indexes
            # never count the same segments twice.
            await self._repository.add_chunk_count(session, kb_id=kb.kb_id, delta=segments)
        return True

    async def _emit_terminal_event(
        self, kb_id: str, document_id: str, status: DocumentStatus, error: str | None
    ) -> None:
        if status == "completed":
            await self._bus.emit(DOCUMENT_INDEXED_EVENT, kb_id=kb_id, document_id=document_id)
        else:
            await self._bus.emit(
                DOCUMENT_INDEX_FAILED_EVENT,
                kb_id=kb_id,
                document_id=document_id,
                error=error,
            )

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


def _citation_kb_name(record: dict[str, Any], kb_name: str | None) -> str | None:
    # Retrieve targets one dataset, so a record without dataset_name still
    # belongs to the KB under test; map its dataset_id to the registry name.
    dataset_name = record.get("dataset_name")
    return str(dataset_name) if dataset_name else kb_name


def _require_document_id(document_id: str) -> None:
    """Refuse ids that cannot address one Dify document path segment.

    httpx normalizes RFC 3986 dot-segments and treats a raw ``?`` as the query
    separator, so an id carrying ``..`` or a separator would retarget the
    upstream call at a different Dify endpoint — deleting the whole dataset,
    for the delete call.
    """
    invalid = (
        not document_id
        or document_id in {".", ".."}
        or "/" in document_id
        or "\\" in document_id
    )
    if invalid:
        raise AppError(404, "DOCUMENT_NOT_FOUND", "Document not found")


def _document_operation_error(exc: DifyConsoleError, *, code: str) -> AppError:
    """Translate a Dify dataset-document failure into the platform error envelope.

    Only 4xx that fault the *request* map to 400, carrying Dify's ``message``
    for the frontend to render — e.g. deleting a document it is still indexing
    fails with "Cannot delete document during indexing.". A rejected session
    (401/403), throttling (429) or any upstream fault is a Dify-side failure
    and must not read as "the user's request was wrong".
    """
    if exc.status_code == 404:
        return AppError(404, "DOCUMENT_NOT_FOUND", "Document not found")
    if exc.status_code in (401, 403):
        return AppError(502, "DIFY_ERROR", "Dify request failed")
    if exc.status_code == 429:
        return AppError(503, "DIFY_RATE_LIMITED", "Dify rate limited the request")
    if 400 <= exc.status_code < 500:
        return AppError(400, code, _dify_error_message(exc.body))
    return AppError(502, "DIFY_ERROR", "Dify request failed")


def _dify_error_message(body: str) -> str:
    """Dify's ``message`` field when the body carries one, else fixed text.

    Non-JSON bodies (a proxy or gateway HTML error page) are never echoed back
    into the API response — the caller only learns that Dify rejected it.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message:
            return message
    return "Dify rejected the request"
