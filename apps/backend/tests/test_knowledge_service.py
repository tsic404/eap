"""KnowledgeService tests: orchestration, validation, events, retrieval scoring."""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.datastructures import UploadFile

from app.config import Settings
from app.dify_console import DifyConsoleError
from app.errors import AppError
from app.events.bus import EventBus
from app.events.knowledge import (
    DOCUMENT_INDEX_FAILED_EVENT,
    DOCUMENT_INDEXED_EVENT,
    DOCUMENT_UPLOADED_EVENT,
    KB_CREATED_EVENT,
    KB_DELETED_EVENT,
)
from app.files import MAX_FILE_SIZE_BYTES
from app.models.agent import AgentKnowledgeBinding
from app.models.knowledge import KnowledgeBaseRegistry, KnowledgeDocument
from app.models.tenant import Tenant
from app.schemas.knowledge import CreateKnowledgeBaseDto, DocumentDto, RetrieveTestDto
from app.services.dify_client import DifyClientService
from app.services.knowledge import KnowledgeService

_Event = tuple[str, dict[str, Any]]


def _tenant() -> Tenant:
    return Tenant(
        id=uuid.uuid4(),
        name="Tenant",
        slug=f"t-{uuid.uuid4().hex[:8]}",
        sso_provider="local",
        status="active",
        quota_limit=10000,
        quota_used=0,
    )


def _kb(
    tenant: Tenant,
    *,
    kb_id: str = "kb-1",
    dify_dataset_id: str = "ds-1",
    dify_api_key: str | None = "ds-secret",
    bindings: tuple[str, ...] = (),
) -> KnowledgeBaseRegistry:
    kb = KnowledgeBaseRegistry(
        kb_id=kb_id,
        tenant_id=tenant.id,
        dify_dataset_id=dify_dataset_id,
        dify_api_key=dify_api_key,
        name="Knowledge",
        description=None,
        type="business",
        indexing_status="ready",
        doc_count=0,
        chunk_count=0,
        created_at=datetime.now(UTC),
    )
    for agent_id in bindings:
        kb.agent_bindings.append(AgentKnowledgeBinding(agent_id=agent_id, kb_id=kb_id))
    return kb


def _doc(kb_id: str, document_id: str, status: str) -> KnowledgeDocument:
    return KnowledgeDocument(
        kb_id=kb_id,
        document_id=document_id,
        name="report.pdf",
        status=status,
        created_at=datetime.now(UTC),
    )


class _FakeRepository:
    """In-memory stand-in for ``KnowledgeRepository`` (no live DB in unit tests)."""

    def __init__(
        self,
        kbs: tuple[KnowledgeBaseRegistry, ...] = (),
        documents: tuple[KnowledgeDocument, ...] = (),
    ) -> None:
        self._kbs = {kb.kb_id: kb for kb in kbs}
        self._documents = {(d.kb_id, d.document_id): d for d in documents}
        self.deleted: list[str] = []
        self.chunk_additions: list[tuple[str, int]] = []
        self.document_additions: list[str] = []
        self.releases: list[tuple[str, int]] = []

    async def get_for_tenant(
        self, session: Any, kb_id: str, tenant_id: uuid.UUID
    ) -> KnowledgeBaseRegistry | None:
        kb = self._kbs.get(kb_id)
        if kb is not None and kb.tenant_id == tenant_id:
            return kb
        return None

    async def get_by_name_for_tenant(
        self, session: Any, tenant_id: uuid.UUID, name: str
    ) -> KnowledgeBaseRegistry | None:
        for kb in self._kbs.values():
            if kb.tenant_id == tenant_id and kb.name == name:
                return kb
        return None

    async def list_for_tenant(
        self, session: Any, tenant_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[list[KnowledgeBaseRegistry], int]:
        rows = [kb for kb in self._kbs.values() if kb.tenant_id == tenant_id]
        return rows[offset : offset + limit], len(rows)

    async def create(
        self,
        session: Any,
        *,
        tenant_id: uuid.UUID,
        dify_dataset_id: str,
        dify_api_key: str | None,
        name: str,
        description: str | None,
        type: str,
    ) -> KnowledgeBaseRegistry:
        kb = KnowledgeBaseRegistry(
            kb_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            dify_dataset_id=dify_dataset_id,
            dify_api_key=dify_api_key,
            name=name,
            description=description,
            type=type,
            indexing_status="ready",
            doc_count=0,
            chunk_count=0,
            created_at=datetime.now(UTC),
        )
        self._kbs[kb.kb_id] = kb
        return kb

    async def delete(self, session: Any, kb: KnowledgeBaseRegistry) -> None:
        self.deleted.append(kb.kb_id)
        self._kbs.pop(kb.kb_id, None)

    async def insert_document(
        self, session: Any, *, kb_id: str, document_id: str, name: str
    ) -> None:
        self._documents[(kb_id, document_id)] = KnowledgeDocument(
            kb_id=kb_id,
            document_id=document_id,
            name=name,
            status="indexing",
            created_at=datetime.now(UTC),
        )

    async def transition_document_status(
        self,
        session: Any,
        *,
        kb_id: str,
        document_id: str,
        status: str,
        error: str | None = None,
    ) -> bool:
        doc = self._documents.get((kb_id, document_id))
        if doc is None or doc.status == status:
            return False
        doc.status = status
        doc.error = error
        return True

    async def get_document(
        self, session: Any, *, kb_id: str, document_id: str
    ) -> KnowledgeDocument | None:
        return self._documents.get((kb_id, document_id))

    async def add_chunk_count(self, session: Any, *, kb_id: str, delta: int) -> None:
        self.chunk_additions.append((kb_id, delta))
        kb = self._kbs.get(kb_id)
        if kb is not None:
            kb.chunk_count += delta

    async def add_document_count(self, session: Any, *, kb_id: str) -> None:
        self.document_additions.append(kb_id)
        kb = self._kbs.get(kb_id)
        if kb is not None:
            kb.doc_count += 1

    async def release_document_counts(self, session: Any, *, kb_id: str, segments: int) -> None:
        self.releases.append((kb_id, segments))
        kb = self._kbs.get(kb_id)
        if kb is not None:
            kb.doc_count = max(kb.doc_count - 1, 0)
            kb.chunk_count = max(kb.chunk_count - segments, 0)

    async def delete_document(self, session: Any, *, kb_id: str, document_id: str) -> bool:
        return self._documents.pop((kb_id, document_id), None) is not None


def _collector() -> tuple[list[_Event], Any]:
    events: list[_Event] = []

    async def handler(sender: str, **payload: Any) -> None:
        events.append((sender, payload))

    return events, handler


def _service(
    dify: AsyncMock,
    *,
    repo: _FakeRepository | None = None,
    bus: EventBus | None = None,
    retriever: DifyClientService | None = None,
) -> KnowledgeService:
    return KnowledgeService(
        dify,
        settings=Settings(_env_file=None),
        repository=repo if repo is not None else _FakeRepository(),
        event_bus=bus if bus is not None else EventBus(),
        retriever=retriever,
    )


def _upload(filename: str, content: bytes, *, size: int | None = None) -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename, size=size)


@pytest.mark.asyncio
async def test_create_creates_dataset_and_emits_event() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.create_dataset.return_value = {"id": "ds-1"}
    dify.get_dataset_api_keys.return_value = {"data": [{"id": "key-1", "token": "ds-secret"}]}
    events, handler = _collector()
    bus = EventBus()
    bus.subscribe(KB_CREATED_EVENT, handler)
    service = _service(dify, bus=bus)

    result = await service.create(
        AsyncMock(), tenant, CreateKnowledgeBaseDto(name="KB", description="d")
    )

    assert result.name == "KB"
    assert result.kb_id
    dify.create_dataset.assert_awaited_once()
    dify.get_dataset_api_keys.assert_awaited_once()
    dify.create_dataset_api_key.assert_not_awaited()
    assert events == [
        (
            KB_CREATED_EVENT,
            {"kb_id": result.kb_id, "tenant_id": tenant.id, "dify_dataset_id": "ds-1"},
        )
    ]


@pytest.mark.asyncio
async def test_create_creates_dataset_api_key_when_none_exists() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.create_dataset.return_value = {"id": "ds-1"}
    dify.get_dataset_api_keys.return_value = {"data": []}
    dify.create_dataset_api_key.return_value = {"id": "key-1", "token": "ds-secret"}
    service = _service(dify, repo=_FakeRepository())

    result = await service.create(AsyncMock(), tenant, CreateKnowledgeBaseDto(name="KB"))

    assert result.kb_id
    dify.get_dataset_api_keys.assert_awaited_once()
    dify.create_dataset_api_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_compensates_deletes_dataset_on_commit_failure() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.create_dataset.return_value = {"id": "ds-1"}
    dify.get_dataset_api_keys.return_value = {"data": [{"id": "key-1", "token": "ds-secret"}]}
    session = AsyncMock()
    session.commit.side_effect = RuntimeError("db down")
    service = _service(dify)

    with pytest.raises(RuntimeError):
        await service.create(session, tenant, CreateKnowledgeBaseDto(name="KB"))

    dify.delete_dataset.assert_awaited_once_with("ds-1")


@pytest.mark.asyncio
async def test_create_rejects_duplicate_name_before_dify_call() -> None:
    tenant = _tenant()
    repo = _FakeRepository((_kb(tenant),))  # existing KB named "Knowledge"
    dify = AsyncMock()
    service = _service(dify, repo=repo)

    with pytest.raises(AppError) as exc_info:
        await service.create(AsyncMock(), tenant, CreateKnowledgeBaseDto(name="Knowledge"))

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "KB_NAME_EXISTS"
    assert exc_info.value.message == "知识库名称已存在"
    dify.create_dataset.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_maps_dify_name_conflict_to_409() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.create_dataset.side_effect = DifyConsoleError(409, "Dataset name already exists")
    service = _service(dify)

    with pytest.raises(AppError) as exc_info:
        await service.create(AsyncMock(), tenant, CreateKnowledgeBaseDto(name="KB"))

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "KB_NAME_EXISTS"
    assert exc_info.value.message == "知识库名称已存在"
    # The dataset was never created, so there is nothing to compensate.
    dify.delete_dataset.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_propagates_non_conflict_dify_error() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.create_dataset.side_effect = DifyConsoleError(503, "upstream down")
    service = _service(dify)

    with pytest.raises(DifyConsoleError):
        await service.create(AsyncMock(), tenant, CreateKnowledgeBaseDto(name="KB"))

    dify.delete_dataset.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_returns_paginated_structure_with_total() -> None:
    tenant = _tenant()
    repo = _FakeRepository((_kb(tenant, kb_id="kb-1"), _kb(tenant, kb_id="kb-2")))
    service = _service(AsyncMock(), repo=repo)

    page = await service.list_knowledge_bases(AsyncMock(), tenant, offset=0, limit=10)

    assert page.total == 2
    assert {kb.kb_id for kb in page.items} == {"kb-1", "kb-2"}


@pytest.mark.asyncio
async def test_list_documents_returns_page_with_total() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.list_documents.return_value = {
        "data": [
            {"id": "doc-1", "name": "a.pdf", "indexing_status": "completed"},
            {"id": "doc-2", "name": "b.pdf", "indexing_status": "indexing"},
        ],
        "total": 7,
    }
    service = _service(dify, repo=_FakeRepository((_kb(tenant),)))

    page = await service.list_documents(AsyncMock(), tenant, "kb-1", page=1, limit=2)

    assert page.total == 7
    assert [d.id for d in page.items] == ["doc-1", "doc-2"]
    assert page.items[0].status == "completed"
    # No local row for either document: nothing to record, nothing fetched.
    dify.get_document_indexing_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_documents_records_completion_and_chunk_count() -> None:
    tenant = _tenant()
    kb = _kb(tenant)
    dify = AsyncMock()
    dify.list_documents.return_value = {
        "data": [{"id": "doc-1", "name": "kb-sample.txt", "indexing_status": "completed"}],
        "total": 1,
    }
    dify.get_document_indexing_status.return_value = {
        "indexing_status": "completed",
        "total_segments": 5,
    }
    events, handler = _collector()
    bus = EventBus()
    bus.subscribe(DOCUMENT_INDEXED_EVENT, handler)
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "indexing"),))
    service = _service(dify, repo=repo, bus=bus)

    page = await service.list_documents(AsyncMock(), tenant, "kb-1", page=1, limit=20)

    assert [d.status for d in page.items] == ["completed"]
    dify.get_document_indexing_status.assert_awaited_once_with("ds-1", "doc-1")
    assert repo.chunk_additions == [("kb-1", 5)]
    assert kb.chunk_count == 5
    assert events == [(DOCUMENT_INDEXED_EVENT, {"kb_id": "kb-1", "document_id": "doc-1"})]


@pytest.mark.asyncio
async def test_list_documents_does_not_recount_recorded_completion() -> None:
    tenant = _tenant()
    kb = _kb(tenant)
    kb.chunk_count = 5
    dify = AsyncMock()
    dify.list_documents.return_value = {
        "data": [{"id": "doc-1", "name": "a.pdf", "indexing_status": "completed"}],
        "total": 1,
    }
    events, handler = _collector()
    bus = EventBus()
    bus.subscribe(DOCUMENT_INDEXED_EVENT, handler)
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "completed"),))
    service = _service(dify, repo=repo, bus=bus)

    await service.list_documents(AsyncMock(), tenant, "kb-1", page=1, limit=20)

    assert repo.chunk_additions == []
    assert kb.chunk_count == 5
    assert events == []
    dify.get_document_indexing_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_documents_records_failure_without_counting() -> None:
    tenant = _tenant()
    kb = _kb(tenant)
    dify = AsyncMock()
    dify.list_documents.return_value = {
        "data": [
            {
                "id": "doc-1",
                "name": "a.pdf",
                "indexing_status": "error",
                "error": "parse failed",
            }
        ],
        "total": 1,
    }
    events, handler = _collector()
    bus = EventBus()
    bus.subscribe(DOCUMENT_INDEX_FAILED_EVENT, handler)
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "indexing"),))
    service = _service(dify, repo=repo, bus=bus)

    await service.list_documents(AsyncMock(), tenant, "kb-1", page=1, limit=20)

    assert repo.chunk_additions == []
    assert kb.chunk_count == 0
    assert events == [
        (
            DOCUMENT_INDEX_FAILED_EVENT,
            {"kb_id": "kb-1", "document_id": "doc-1", "error": "parse failed"},
        )
    ]


@pytest.mark.asyncio
async def test_delete_rejects_bound_kb() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    service = _service(dify, repo=_FakeRepository((_kb(tenant, bindings=("agent-1",)),)))

    with pytest.raises(AppError) as exc:
        await service.delete(AsyncMock(), tenant, "kb-1")

    assert exc.value.status_code == 409
    assert exc.value.code == "KB_IN_USE"
    dify.delete_dataset.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_removes_kb_and_emits_event() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    repo = _FakeRepository((_kb(tenant),))
    events, handler = _collector()
    bus = EventBus()
    bus.subscribe(KB_DELETED_EVENT, handler)
    service = _service(dify, repo=repo, bus=bus)

    await service.delete(AsyncMock(), tenant, "kb-1")

    dify.delete_dataset.assert_awaited_once_with("ds-1")
    assert repo.deleted == ["kb-1"]
    assert events == [
        (KB_DELETED_EVENT, {"kb_id": "kb-1", "tenant_id": tenant.id, "dify_dataset_id": "ds-1"})
    ]


@pytest.mark.asyncio
async def test_delete_ignores_missing_dify_dataset() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.delete_dataset.side_effect = DifyConsoleError(404, "not found")
    repo = _FakeRepository((_kb(tenant),))
    service = _service(dify, repo=repo)

    await service.delete(AsyncMock(), tenant, "kb-1")

    assert repo.deleted == ["kb-1"]


@pytest.mark.asyncio
async def test_upload_document_rejects_unknown_extension() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    service = _service(dify, repo=_FakeRepository((_kb(tenant),)))

    with pytest.raises(AppError) as exc:
        await service.upload_document(
            AsyncMock(), tenant, "kb-1", _upload("malware.exe", b"MZ\x90\x00")
        )

    assert exc.value.status_code == 422
    assert exc.value.code == "UNSUPPORTED_FORMAT"
    dify.upload_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_document_rejects_oversized_file() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    service = _service(dify, repo=_FakeRepository((_kb(tenant),)))

    with pytest.raises(AppError) as exc:
        await service.upload_document(
            AsyncMock(), tenant, "kb-1", _upload("big.txt", b"a" * (MAX_FILE_SIZE_BYTES + 1))
        )

    assert exc.value.status_code == 422
    assert exc.value.code == "FILE_TOO_LARGE"
    dify.upload_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_document_prechecks_content_length_before_read() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    service = _service(dify, repo=_FakeRepository((_kb(tenant),)))
    # Small actual content but an oversized declared Content-Length: the guard
    # must reject before reading/uploading anything.
    upload = _upload("big.txt", b"tiny", size=MAX_FILE_SIZE_BYTES + 1)

    with pytest.raises(AppError) as exc:
        await service.upload_document(AsyncMock(), tenant, "kb-1", upload)

    assert exc.value.code == "FILE_TOO_LARGE"
    dify.upload_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_document_success_returns_indexing_and_emits_event() -> None:
    tenant = _tenant()
    kb = _kb(tenant)
    dify = AsyncMock()
    dify.upload_file.return_value = {"id": "file-1"}
    dify.create_document.return_value = {
        "documents": [{"id": "doc-1", "indexing_status": "indexing"}]
    }
    events, handler = _collector()
    bus = EventBus()
    bus.subscribe(DOCUMENT_UPLOADED_EVENT, handler)
    repo = _FakeRepository((kb,))
    service = _service(dify, repo=repo, bus=bus)

    result = await service.upload_document(
        AsyncMock(), tenant, "kb-1", _upload("report.pdf", b"%PDF-1.7 content")
    )

    assert result == DocumentDto(id="doc-1", name="report.pdf", status="indexing")
    dify.upload_file.assert_awaited_once()
    dify.create_document.assert_awaited_once()
    # The count moves through the repository's SQL increment, not an ORM
    # read-modify-write on the KB row.
    assert repo.document_additions == ["kb-1"]
    assert kb.doc_count == 1
    stored = repo._documents.get(("kb-1", "doc-1"))
    assert stored is not None and stored.status == "indexing"
    assert events == [
        (
            DOCUMENT_UPLOADED_EVENT,
            {"kb_id": "kb-1", "document_id": "doc-1", "document_name": "report.pdf"},
        )
    ]


@pytest.mark.asyncio
async def test_get_document_status_emits_indexed_on_transition() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.get_document_indexing_status.return_value = {
        "indexing_status": "completed",
        "total_segments": 5,
    }
    events, handler = _collector()
    bus = EventBus()
    bus.subscribe(DOCUMENT_INDEXED_EVENT, handler)
    kb = _kb(tenant)
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "indexing"),))
    service = _service(dify, repo=repo, bus=bus)

    result = await service.get_document_status(AsyncMock(), tenant, "kb-1", "doc-1")

    assert result.status == "completed"
    assert events == [(DOCUMENT_INDEXED_EVENT, {"kb_id": "kb-1", "document_id": "doc-1"})]
    assert repo.chunk_additions == [("kb-1", 5)]
    assert kb.chunk_count == 5


@pytest.mark.asyncio
async def test_get_document_status_accumulates_chunk_count_across_documents() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.get_document_indexing_status.return_value = {
        "indexing_status": "completed",
        "total_segments": 3,
    }
    kb = _kb(tenant)
    # doc-1 already contributed its 4 segments; doc-2's completion adds on top
    # rather than replacing the KB-wide total.
    kb.chunk_count = 4
    repo = _FakeRepository(
        (kb,),
        (_doc("kb-1", "doc-1", "completed"), _doc("kb-1", "doc-2", "indexing")),
    )
    service = _service(dify, repo=repo)

    await service.get_document_status(AsyncMock(), tenant, "kb-1", "doc-2")

    assert kb.chunk_count == 7


@pytest.mark.asyncio
async def test_get_document_status_counts_reindexed_document_once() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.get_document_indexing_status.return_value = {
        "indexing_status": "completed",
        "total_segments": 4,
    }
    kb = _kb(tenant)
    # A failed first attempt contributed nothing; the re-index that completes
    # contributes its segments exactly once.
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "failed"),))
    service = _service(dify, repo=repo)

    await service.get_document_status(AsyncMock(), tenant, "kb-1", "doc-1")

    assert repo.chunk_additions == [("kb-1", 4)]
    assert kb.chunk_count == 4


@pytest.mark.asyncio
async def test_get_document_status_does_not_reemit_on_repeat_poll() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.get_document_indexing_status.return_value = {
        "indexing_status": "completed",
        "total_segments": 5,
    }
    events, handler = _collector()
    bus = EventBus()
    bus.subscribe(DOCUMENT_INDEXED_EVENT, handler)
    kb = _kb(tenant)
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "completed"),))
    service = _service(dify, repo=repo, bus=bus)

    result = await service.get_document_status(AsyncMock(), tenant, "kb-1", "doc-1")

    assert result.status == "completed"
    assert events == []
    assert repo.chunk_additions == []
    assert kb.chunk_count == 0


@pytest.mark.asyncio
async def test_get_document_status_without_segment_count_keeps_chunk_count() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    # A payload without the segment count (Dify omits it when it cannot report
    # one) must leave the recorded total alone rather than zeroing it.
    dify.get_document_indexing_status.return_value = {"indexing_status": "completed"}
    kb = _kb(tenant)
    kb.chunk_count = 5
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "indexing"),))
    service = _service(dify, repo=repo)

    result = await service.get_document_status(AsyncMock(), tenant, "kb-1", "doc-1")

    assert result.status == "completed"
    assert kb.chunk_count == 5


@pytest.mark.asyncio
async def test_get_document_status_emits_index_failed_on_error() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.get_document_indexing_status.return_value = {"indexing_status": "error", "error": "boom"}
    events, handler = _collector()
    bus = EventBus()
    bus.subscribe(DOCUMENT_INDEX_FAILED_EVENT, handler)
    kb = _kb(tenant)
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "indexing"),))
    service = _service(dify, repo=repo, bus=bus)

    result = await service.get_document_status(AsyncMock(), tenant, "kb-1", "doc-1")

    assert result.status == "failed"
    assert result.error == "boom"
    assert events == [
        (DOCUMENT_INDEX_FAILED_EVENT, {"kb_id": "kb-1", "document_id": "doc-1", "error": "boom"})
    ]
    assert repo.chunk_additions == []
    assert kb.chunk_count == 0


@pytest.mark.asyncio
async def test_retry_document_wraps_single_id_in_document_ids() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    service = _service(
        dify, repo=_FakeRepository((_kb(tenant),), (_doc("kb-1", "doc-1", "failed"),))
    )

    await service.retry_document(AsyncMock(), tenant, "kb-1", "doc-1")

    # Dify's retry endpoint only accepts the batch shape; a single document is
    # still sent as a one-element list.
    dify.retry_document_indexing.assert_awaited_once_with("ds-1", ["doc-1"])


@pytest.mark.asyncio
async def test_retry_document_maps_dify_rejection_to_400_with_message() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.retry_document_indexing.side_effect = DifyConsoleError(
        400, json.dumps({"message": "Document is already completed."})
    )
    service = _service(dify, repo=_FakeRepository((_kb(tenant),)))

    with pytest.raises(AppError) as exc:
        await service.retry_document(AsyncMock(), tenant, "kb-1", "doc-1")

    assert exc.value.status_code == 400
    assert exc.value.code == "DOCUMENT_RETRY_FAILED"
    assert exc.value.message == "Document is already completed."


@pytest.mark.asyncio
async def test_retry_document_maps_missing_document_to_404() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.retry_document_indexing.side_effect = DifyConsoleError(
        404, json.dumps({"message": "Document Not Exists."})
    )
    service = _service(dify, repo=_FakeRepository((_kb(tenant),)))

    with pytest.raises(AppError) as exc:
        await service.retry_document(AsyncMock(), tenant, "kb-1", "doc-1")

    assert exc.value.status_code == 404
    assert exc.value.code == "DOCUMENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_delete_document_retires_local_row_and_kb_counts() -> None:
    tenant = _tenant()
    kb = _kb(tenant)
    kb.doc_count = 1
    kb.chunk_count = 5
    dify = AsyncMock()
    dify.get_document_indexing_status.return_value = {
        "indexing_status": "completed",
        "total_segments": 5,
    }
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "completed"),))
    service = _service(dify, repo=repo)

    await service.delete_document(AsyncMock(), tenant, "kb-1", "doc-1")

    dify.delete_document.assert_awaited_once_with("ds-1", "doc-1")
    assert ("kb-1", "doc-1") not in repo._documents
    assert repo.releases == [("kb-1", 5)]
    assert kb.doc_count == 0
    assert kb.chunk_count == 0


@pytest.mark.asyncio
async def test_delete_document_reclaims_no_segments_for_unfinished_document() -> None:
    """A document that never completed contributed nothing, so nothing comes back."""
    tenant = _tenant()
    kb = _kb(tenant)
    kb.doc_count = 1
    kb.chunk_count = 4
    dify = AsyncMock()
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "failed"),))
    service = _service(dify, repo=repo)

    await service.delete_document(AsyncMock(), tenant, "kb-1", "doc-1")

    assert repo.releases == [("kb-1", 0)]
    assert kb.doc_count == 0
    assert kb.chunk_count == 4
    # Nothing was counted, so there is no segment count to read from Dify.
    dify.get_document_indexing_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_document_reclaims_nothing_when_completion_is_unobserved() -> None:
    """Dify finished the document but the platform never recorded it as completed.

    The accumulator only adds on a recorded transition, so the delete must not
    deduct segments for a document that was never counted — the per-document
    status read is skipped entirely.
    """
    tenant = _tenant()
    kb = _kb(tenant)
    kb.doc_count = 1
    kb.chunk_count = 3
    dify = AsyncMock()
    dify.get_document_indexing_status.return_value = {
        "indexing_status": "completed",
        "total_segments": 5,
    }
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "indexing"),))
    service = _service(dify, repo=repo)

    await service.delete_document(AsyncMock(), tenant, "kb-1", "doc-1")

    dify.delete_document.assert_awaited_once_with("ds-1", "doc-1")
    dify.get_document_indexing_status.assert_not_awaited()
    assert repo.releases == [("kb-1", 0)]
    assert kb.doc_count == 0
    assert kb.chunk_count == 3


@pytest.mark.asyncio
async def test_delete_document_leaves_counts_alone_without_local_row() -> None:
    """A document the platform never tracked was never counted either."""
    tenant = _tenant()
    kb = _kb(tenant)
    kb.doc_count = 0
    kb.chunk_count = 5
    dify = AsyncMock()
    repo = _FakeRepository((kb,))  # no local document row
    service = _service(dify, repo=repo)

    await service.delete_document(AsyncMock(), tenant, "kb-1", "doc-1")

    dify.delete_document.assert_awaited_once_with("ds-1", "doc-1")
    dify.get_document_indexing_status.assert_not_awaited()
    assert repo.releases == []
    assert kb.doc_count == 0
    assert kb.chunk_count == 5


@pytest.mark.asyncio
async def test_delete_document_maps_indexing_conflict_to_400() -> None:
    tenant = _tenant()
    kb = _kb(tenant)
    kb.doc_count = 1
    dify = AsyncMock()
    dify.delete_document.side_effect = DifyConsoleError(
        400,
        json.dumps(
            {
                "code": "document_indexing_error",
                "message": "Cannot delete document during indexing.",
            }
        ),
    )
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "indexing"),))
    service = _service(dify, repo=repo)

    with pytest.raises(AppError) as exc:
        await service.delete_document(AsyncMock(), tenant, "kb-1", "doc-1")

    assert exc.value.status_code == 400
    assert exc.value.code == "DOCUMENT_DELETE_FAILED"
    assert exc.value.message == "Cannot delete document during indexing."
    assert ("kb-1", "doc-1") in repo._documents
    assert kb.doc_count == 1


@pytest.mark.asyncio
async def test_delete_document_keeps_local_state_when_dify_fails() -> None:
    tenant = _tenant()
    kb = _kb(tenant)
    kb.doc_count = 1
    dify = AsyncMock()
    dify.delete_document.side_effect = DifyConsoleError(503, "dify unavailable")
    repo = _FakeRepository((kb,), (_doc("kb-1", "doc-1", "failed"),))
    service = _service(dify, repo=repo)

    with pytest.raises(AppError) as exc:
        await service.delete_document(AsyncMock(), tenant, "kb-1", "doc-1")

    assert exc.value.status_code == 502
    assert exc.value.code == "DIFY_ERROR"
    assert ("kb-1", "doc-1") in repo._documents
    assert kb.doc_count == 1


@pytest.mark.asyncio
async def test_delete_document_maps_missing_document_to_404_before_deleting() -> None:
    """A counted document that vanished in Dify surfaces as 404 before any delete."""
    tenant = _tenant()
    dify = AsyncMock()
    dify.get_document_indexing_status.side_effect = DifyConsoleError(
        404, json.dumps({"message": "Document Not Exists."})
    )
    repo = _FakeRepository(
        (_kb(tenant),), (_doc("kb-1", "doc-1", "completed"),)
    )
    service = _service(dify, repo=repo)

    with pytest.raises(AppError) as exc:
        await service.delete_document(AsyncMock(), tenant, "kb-1", "doc-1")

    assert exc.value.status_code == 404
    assert exc.value.code == "DOCUMENT_NOT_FOUND"
    dify.delete_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_operations_reject_ids_that_escape_the_path_segment() -> None:
    """``..``/separators must never reach Dify: httpx would retarget the call."""
    tenant = _tenant()
    dify = AsyncMock()
    service = _service(dify, repo=_FakeRepository((_kb(tenant),)))

    for bad_id in ("..", ".", "a/b", "a\\b", ""):
        for call in (service.retry_document, service.delete_document, service.get_document_status):
            with pytest.raises(AppError) as exc:
                await call(AsyncMock(), tenant, "kb-1", bad_id)
            assert exc.value.status_code == 404
            assert exc.value.code == "DOCUMENT_NOT_FOUND"

    dify.retry_document_indexing.assert_not_awaited()
    dify.delete_document.assert_not_awaited()
    dify.get_document_indexing_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_operations_map_upstream_failures_away_from_400() -> None:
    """A rejected session or throttling is a Dify fault, not a bad request."""
    tenant = _tenant()
    cases = ((401, 502, "DIFY_ERROR"), (403, 502, "DIFY_ERROR"), (429, 503, "DIFY_RATE_LIMITED"))

    for status_code, expected_status, expected_code in cases:
        dify = AsyncMock()
        dify.retry_document_indexing.side_effect = DifyConsoleError(status_code, "upstream")
        service = _service(dify, repo=_FakeRepository((_kb(tenant),)))

        with pytest.raises(AppError) as exc:
            await service.retry_document(AsyncMock(), tenant, "kb-1", "doc-1")

        assert exc.value.status_code == expected_status
        assert exc.value.code == expected_code


@pytest.mark.asyncio
async def test_document_operations_never_echo_a_non_json_upstream_body() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.delete_document.side_effect = DifyConsoleError(
        400, "<html><body>502 Bad Gateway</body></html>"
    )
    repo = _FakeRepository((_kb(tenant),), (_doc("kb-1", "doc-1", "failed"),))
    service = _service(dify, repo=repo)

    with pytest.raises(AppError) as exc:
        await service.delete_document(AsyncMock(), tenant, "kb-1", "doc-1")

    assert exc.value.status_code == 400
    assert exc.value.message == "Dify rejected the request"
    assert "Bad Gateway" not in exc.value.message


@pytest.mark.asyncio
async def test_retrieval_test_returns_citations_score_and_latency() -> None:
    tenant = _tenant()
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "records": [
                    {
                        "segment": {"content": "chunk one", "document": {"name": "report.pdf"}},
                        "score": 0.98,
                        "dataset_name": "DS Alpha",
                    },
                    {
                        "segment": {"content": "chunk two", "document": {"name": "report.pdf"}},
                        "score": 0.55,
                    },
                ]
            },
        )

    retriever = DifyClientService("http://dify.test", "key", transport=httpx.MockTransport(handler))
    service = _service(AsyncMock(), repo=_FakeRepository((_kb(tenant),)), retriever=retriever)

    result = await service.retrieval_test(
        AsyncMock(), tenant, "kb-1", RetrieveTestDto(query="what is eap?")
    )

    assert captured["url"] == "http://dify.test/v1/datasets/ds-1/retrieve"
    assert captured["body"] == {"query": "what is eap?"}
    assert [c.content for c in result.citations] == ["chunk one", "chunk two"]
    assert result.citations[0].score == 0.98
    assert result.citations[0].source_name == "report.pdf"
    assert result.citations[0].kb_name == "DS Alpha"
    assert result.citations[1].kb_name == "Knowledge"
    assert result.score == 0.98
    assert result.latencyMs >= 0
    await retriever.aclose()


@pytest.mark.asyncio
async def test_retrieval_test_uses_dataset_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = _tenant()
    captured: dict[str, str] = {}

    class _FakeRetriever:
        def __init__(self, base_url: str, api_key: str) -> None:
            captured["base_url"] = base_url
            captured["api_key"] = api_key

        async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
            return {"records": []}

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("app.services.knowledge.DifyClientService", _FakeRetriever)
    service = _service(AsyncMock(), repo=_FakeRepository((_kb(tenant, dify_api_key="ds-secret"),)))

    await service.retrieval_test(AsyncMock(), tenant, "kb-1", RetrieveTestDto(query="q"))

    assert captured["api_key"] == "ds-secret"


@pytest.mark.asyncio
async def test_retrieval_test_rejects_missing_api_key() -> None:
    tenant = _tenant()
    service = _service(AsyncMock(), repo=_FakeRepository((_kb(tenant, dify_api_key=None),)))

    with pytest.raises(AppError) as exc:
        await service.retrieval_test(AsyncMock(), tenant, "kb-1", RetrieveTestDto(query="q"))

    assert exc.value.code == "KB_API_KEY_MISSING"


@pytest.mark.asyncio
async def test_get_unknown_kb_raises_404() -> None:
    tenant = _tenant()
    service = _service(AsyncMock(), repo=_FakeRepository())

    with pytest.raises(AppError) as exc:
        await service.get(AsyncMock(), tenant, "missing")

    assert exc.value.status_code == 404
    assert exc.value.code == "KB_NOT_FOUND"
