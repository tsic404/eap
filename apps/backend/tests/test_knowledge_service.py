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
from app.models.knowledge import KnowledgeBaseRegistry
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
    bindings: tuple[str, ...] = (),
) -> KnowledgeBaseRegistry:
    kb = KnowledgeBaseRegistry(
        kb_id=kb_id,
        tenant_id=tenant.id,
        dify_dataset_id=dify_dataset_id,
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


class _FakeRepository:
    """In-memory stand-in for ``KnowledgeRepository`` (no live DB in unit tests)."""

    def __init__(self, kbs: tuple[KnowledgeBaseRegistry, ...] = ()) -> None:
        self._kbs = {kb.kb_id: kb for kb in kbs}
        self.deleted: list[str] = []

    async def get_for_tenant(
        self, session: Any, kb_id: str, tenant_id: uuid.UUID
    ) -> KnowledgeBaseRegistry | None:
        kb = self._kbs.get(kb_id)
        if kb is not None and kb.tenant_id == tenant_id:
            return kb
        return None

    async def create(
        self,
        session: Any,
        *,
        tenant_id: uuid.UUID,
        dify_dataset_id: str,
        name: str,
        description: str | None,
        type: str,
    ) -> KnowledgeBaseRegistry:
        kb = KnowledgeBaseRegistry(
            kb_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            dify_dataset_id=dify_dataset_id,
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


def _upload(filename: str, content: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


@pytest.mark.asyncio
async def test_create_creates_dataset_and_emits_event() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.create_dataset.return_value = {"id": "ds-1"}
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
    assert events == [
        (
            KB_CREATED_EVENT,
            {"kb_id": result.kb_id, "tenant_id": tenant.id, "dify_dataset_id": "ds-1"},
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
    service = _service(dify, repo=_FakeRepository((kb,)), bus=bus)

    result = await service.upload_document(
        AsyncMock(), tenant, "kb-1", _upload("report.pdf", b"%PDF-1.7 content")
    )

    assert result == DocumentDto(id="doc-1", name="report.pdf", status="indexing")
    dify.upload_file.assert_awaited_once()
    dify.create_document.assert_awaited_once()
    assert kb.doc_count == 1
    assert events == [
        (
            DOCUMENT_UPLOADED_EVENT,
            {"kb_id": "kb-1", "document_id": "doc-1", "document_name": "report.pdf"},
        )
    ]


@pytest.mark.asyncio
async def test_get_document_status_emits_indexed_on_completion() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.get_document_indexing_status.return_value = {"indexing_status": "completed"}
    events, handler = _collector()
    bus = EventBus()
    bus.subscribe(DOCUMENT_INDEXED_EVENT, handler)
    service = _service(dify, repo=_FakeRepository((_kb(tenant),)), bus=bus)

    result = await service.get_document_status(AsyncMock(), tenant, "kb-1", "doc-1")

    assert result.status == "completed"
    assert events == [(DOCUMENT_INDEXED_EVENT, {"kb_id": "kb-1", "document_id": "doc-1"})]


@pytest.mark.asyncio
async def test_get_document_status_emits_index_failed_on_error() -> None:
    tenant = _tenant()
    dify = AsyncMock()
    dify.get_document_indexing_status.return_value = {"indexing_status": "error", "error": "boom"}
    events, handler = _collector()
    bus = EventBus()
    bus.subscribe(DOCUMENT_INDEX_FAILED_EVENT, handler)
    service = _service(dify, repo=_FakeRepository((_kb(tenant),)), bus=bus)

    result = await service.get_document_status(AsyncMock(), tenant, "kb-1", "doc-1")

    assert result.status == "failed"
    assert result.error == "boom"
    assert events == [
        (DOCUMENT_INDEX_FAILED_EVENT, {"kb_id": "kb-1", "document_id": "doc-1", "error": "boom"})
    ]


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
                    },
                    {
                        "segment": {"content": "chunk two", "document": {"name": "report.pdf"}},
                        "score": 0.55,
                    },
                ]
            },
        )

    retriever = DifyClientService(
        "http://dify.test", "key", transport=httpx.MockTransport(handler)
    )
    service = _service(
        AsyncMock(), repo=_FakeRepository((_kb(tenant),)), retriever=retriever
    )

    result = await service.retrieval_test(
        AsyncMock(), tenant, "kb-1", RetrieveTestDto(query="what is eap?")
    )

    assert captured["url"] == "http://dify.test/v1/datasets/ds-1/retrieve"
    assert captured["body"] == {"query": "what is eap?"}
    assert [c.content for c in result.citations] == ["chunk one", "chunk two"]
    assert result.citations[0].score == 0.98
    assert result.citations[0].source_name == "report.pdf"
    assert result.score == 0.98
    assert result.latencyMs >= 0
    await retriever.aclose()


@pytest.mark.asyncio
async def test_get_unknown_kb_raises_404() -> None:
    tenant = _tenant()
    service = _service(AsyncMock(), repo=_FakeRepository())

    with pytest.raises(AppError) as exc:
        await service.get(AsyncMock(), tenant, "missing")

    assert exc.value.status_code == 404
    assert exc.value.code == "KB_NOT_FOUND"
