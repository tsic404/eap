"""Executable acceptance cases for TC-tech-kb-03 (indexing state, deletion, retrieval).

Document/KB status folding and one-shot counting, KB deletion (bound, cascading,
upstream failure), the dataset API key the creation and recall paths do and do not
touch, the recall ordering/score contract, and tenant scoping.

The concurrent first-observation clause runs its two observations as genuinely
concurrent requests on their own connections (see ``_own_connections``); the shared
harness transaction serialises statements, so it cannot express two transactions
racing on one document row.

Deliberately not covered here, so the gaps stay visible instead of being implied:
the C-43 retrieval parameters (result count 1-10 defaulting to 5, score threshold,
query length 1-1000 with the empty-query 422), the auditor's recall-test
entitlement and the employee read baseline (C-36), and the in-tenant 404s for a
document that does not exist (C-45/C-46).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import httpx
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import get_session
from app.dify_console import DifyConsoleError
from app.events.bus import bus
from app.events.knowledge import DOCUMENT_INDEXED_EVENT
from app.models.knowledge import KnowledgeBaseRegistry, KnowledgeDocument
from app.models.tenant import Tenant
from app.models.user import User
from app.services.dify_client import DifyClientService
from app.services.knowledge import KnowledgeService

TC_ID = "TC-tech-kb-03"

_KB_URL = "/api/knowledge-bases"

# Generous enough that a loaded runner cannot fail a race, short enough that a
# genuinely serialised attempt fails the test instead of hanging it.
_UPSTREAM_BARRIER_TIMEOUT_S = 10.0

# The stub upstream spends this long answering, so the reported recall latency has
# a known lower bound instead of a constant that any value satisfies.
_STUB_UPSTREAM_DELAY_S = 0.05

_RetrieveHandler = Callable[[httpx.Request], httpx.Response]


@contextmanager
def _collect(event: str) -> Iterator[list[dict[str, Any]]]:
    """Record every payload emitted on ``event`` for the duration of the block."""
    seen: list[dict[str, Any]] = []

    async def _handler(_name: str, **payload: Any) -> None:
        seen.append(payload)

    bus.subscribe(event, _handler)
    try:
        yield seen
    finally:
        bus.unsubscribe(event, _handler)


@asynccontextmanager
async def _own_connections(api: Any) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Give every request its own connection instead of the fixture's single one.

    The harness binds all request sessions to one connection inside one outer
    transaction, where two writers can never be in flight at the same time.
    This context manager swaps in an engine the test owns, so requests opened
    through it run in independent transactions.
    """
    engine = create_async_engine(api.settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    previous = api.app.dependency_overrides.get(get_session)

    async def _session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    api.app.dependency_overrides[get_session] = _session
    try:
        yield factory
    finally:
        if previous is None:
            api.app.dependency_overrides.pop(get_session, None)
        else:
            api.app.dependency_overrides[get_session] = previous
        await engine.dispose()


async def _commit_indexing_document(
    api: Any, factory: async_sessionmaker[AsyncSession]
) -> tuple[Tenant, User]:
    """Commit a tenant, a KB and one still-indexing document on their own connection."""
    async with factory() as session:
        tenant = Tenant(
            name="Race Tenant",
            slug=f"race-{api.worker_index}-{uuid.uuid4().hex[:8]}",
            sso_provider="local",
            status="active",
        )
        session.add(tenant)
        await session.flush()
        user = User(
            tenant_id=tenant.id,
            sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
            email=f"race-{uuid.uuid4().hex[:8]}@example.com",
            name="Race User",
            role="knowledge_admin",
            status="active",
        )
        session.add(user)
        session.add(
            KnowledgeBaseRegistry(
                kb_id="kb-1",
                tenant_id=tenant.id,
                dify_dataset_id="ds-kb-1",
                dify_api_key="ds-secret",
                name="kb-1",
                type="business",
                indexing_status="ready",
                doc_count=1,
                chunk_count=0,
            )
        )
        session.add(
            KnowledgeDocument(
                kb_id="kb-1", document_id="doc-1", name="notes.txt", status="indexing"
            )
        )
        await session.commit()
    return tenant, user


async def _purge_tenant(factory: async_sessionmaker[AsyncSession], tenant: Tenant) -> None:
    """Undo what a concurrency test committed on its own connections."""
    async with factory() as session:
        # Knowledge documents go with their knowledge base (FK cascade).
        await session.execute(
            delete(KnowledgeBaseRegistry).where(KnowledgeBaseRegistry.tenant_id == tenant.id)
        )
        await session.execute(delete(User).where(User.tenant_id == tenant.id))
        await session.execute(delete(Tenant).where(Tenant.id == tenant.id))
        await session.commit()


def _concurrent_status_polls(
    payload: dict[str, Any], attempts: int = 2
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Answer every status poll only once all of them have arrived.

    The barrier parks each poll in front of the upstream boundary, so the two
    first observations genuinely overlap instead of one being serialised behind
    the other's finished transition.
    """
    barrier = asyncio.Barrier(attempts)

    async def _status(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        await asyncio.wait_for(barrier.wait(), timeout=_UPSTREAM_BARRIER_TIMEOUT_S)
        return payload

    return _status


def _record(score: float, content: str, name: str = "report.txt") -> dict[str, Any]:
    """One Dify retrieve record, shaped the way the recall path reads it."""
    return {"score": score, "segment": {"content": content, "document": {"name": name}}}


def _retrieve_handler(
    records: list[dict[str, Any]] | None = None,
    *,
    status_code: int = 200,
    calls: list[str] | None = None,
    delay: float = 0.0,
) -> _RetrieveHandler:
    def _handle(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.path)
        if delay:
            time.sleep(delay)
        if status_code != 200:
            return httpx.Response(status_code, json={"message": "upstream exploded"})
        return httpx.Response(200, json={"records": records or []})

    return _handle


def _use_stub_retriever(api: Any, handler: _RetrieveHandler) -> None:
    """Route the recall path at a canned Dify Service API response.

    The harness mocks the Dify *console* boundary only; recall goes through the
    Dify Service API, so the app-scoped service is rebuilt with the retriever
    seam ``KnowledgeService`` documents for exactly this purpose.
    """
    api.app.state.knowledge_service = KnowledgeService(
        api.console,
        settings=api.settings,
        retriever=DifyClientService(
            api.settings.dify_api_base_url, "ds-secret", transport=httpx.MockTransport(handler)
        ),
    )


async def _create_kb(api: Any, headers: dict[str, str], name: str) -> str:
    resp = await api.client.post(_KB_URL, headers=headers, json={"name": name})
    assert resp.status_code == 201
    return str(resp.json()["data"]["kb_id"])


async def _upload_document(api: Any, headers: dict[str, str], kb_id: str = "kb-1") -> None:
    resp = await api.client.post(
        f"{_KB_URL}/{kb_id}/documents",
        headers=headers,
        files={"file": ("notes.txt", b"payload", "text/plain")},
    )
    assert resp.status_code == 201


async def _bind_agent(
    api: Any, headers: dict[str, str], kb_id: str, agent_id: str = "kb-bound-agent"
) -> None:
    resp = await api.client.post(
        "/api/agents",
        headers=headers,
        json={"agentId": agent_id, "name": "Bound Agent", "knowledgeBaseIds": [kb_id]},
    )
    assert resp.status_code == 201


async def _document_status(
    api: Any, headers: dict[str, str], kb_id: str = "kb-1", document_id: str = "doc-1"
) -> httpx.Response:
    return await api.client.get(
        f"{_KB_URL}/{kb_id}/documents/{document_id}/status", headers=headers
    )


async def _stored_document_status(api: Any, kb_id: str = "kb-1", document_id: str = "doc-1") -> str:
    """The status the server recorded locally for a document.

    C-45 governs the local record — a retry has to move it back to ``indexing`` —
    so the recorded row, not the upstream folding, is what that clause is read on.
    """
    async with api.db.session() as session:
        row = await session.get(KnowledgeDocument, (kb_id, document_id))
        assert row is not None
        return str(row.status)


async def _kb_detail(api: Any, headers: dict[str, str], kb_id: str = "kb-1") -> dict[str, Any]:
    resp = await api.client.get(f"{_KB_URL}/{kb_id}", headers=headers)
    assert resp.status_code == 200
    return dict(resp.json()["data"])


@pytest.mark.asyncio
async def test_status_endpoint_folds_upstream_indexing_to_indexing(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.return_value = {"indexing_status": "indexing"}

    resp = await _document_status(api, headers)

    assert resp.status_code == 200
    assert resp.json()["data"] == {"id": "doc-1", "status": "indexing", "error": None}


@pytest.mark.asyncio
async def test_status_endpoint_folds_upstream_completion_and_adds_its_segments(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.return_value = {
        "indexing_status": "completed",
        "total_segments": 7,
    }

    resp = await _document_status(api, headers)

    assert resp.json()["data"] == {"id": "doc-1", "status": "completed", "error": None}
    detail = await _kb_detail(api, headers)
    assert detail["chunk_count"] == 7
    assert detail["doc_count"] == 1


@pytest.mark.asyncio
async def test_status_endpoint_folds_upstream_index_error_to_failed(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.return_value = {
        "indexing_status": "error",
        "error": "embedding provider rejected the request",
    }

    resp = await _document_status(api, headers)

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "id": "doc-1",
        "status": "failed",
        "error": "embedding provider rejected the request",
    }
    assert (await _kb_detail(api, headers))["chunk_count"] == 0


@pytest.mark.asyncio
async def test_a_terminal_status_is_counted_and_emitted_once_on_a_later_poll(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.side_effect = [
        {"indexing_status": "completed", "total_segments": 5},
        {"indexing_status": "indexing"},
    ]

    with _collect(DOCUMENT_INDEXED_EVENT) as events:
        first = await _document_status(api, headers)
        await _document_status(api, headers)

    assert first.json()["data"]["status"] == "completed"
    detail = await _kb_detail(api, headers)
    # The recorded completion keeps its segments and its single event; the KB
    # does not fall back to an unfinished state.
    assert detail["chunk_count"] == 5
    assert detail["doc_count"] == 1
    assert len([event for event in events if event.get("kb_id") == "kb-1"]) == 1


@pytest.mark.asyncio
async def test_a_terminal_status_does_not_regress_on_a_later_poll(api) -> None:
    """C-40: a recorded terminal status is never answered as an earlier state."""
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.side_effect = [
        {"indexing_status": "completed", "total_segments": 5},
        {"indexing_status": "indexing"},
    ]
    assert (await _document_status(api, headers)).json()["data"]["status"] == "completed"

    second = await _document_status(api, headers)

    # The upstream's older, unfinished state must not be reported back to the
    # caller once the document's completion has been recorded.
    assert second.json()["data"]["status"] == "completed"


@pytest.mark.asyncio
async def test_a_terminal_status_does_not_regress_in_a_later_listing(api) -> None:
    """C-40: the listing the UI polls holds a recorded terminal state too."""
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.list_documents.side_effect = [
        {
            "data": [{"id": "doc-1", "name": "notes.txt", "indexing_status": "completed"}],
            "total": 1,
        },
        {
            "data": [{"id": "doc-1", "name": "notes.txt", "indexing_status": "indexing"}],
            "total": 1,
        },
    ]

    with _collect(DOCUMENT_INDEXED_EVENT) as events:
        first = await api.client.get(f"{_KB_URL}/kb-1/documents", headers=headers)
        second = await api.client.get(f"{_KB_URL}/kb-1/documents", headers=headers)

    assert first.json()["data"]["items"][0]["status"] == "completed"
    # Same read as the status endpoint's: the record, not the latest upstream
    # state, decides what an already completed document is listed as.
    assert second.json()["data"]["items"][0]["status"] == "completed"
    assert (await _kb_detail(api, headers))["chunk_count"] == 5
    assert len([event for event in events if event.get("kb_id") == "kb-1"]) == 1


@pytest.mark.asyncio
async def test_repeated_observation_of_one_terminal_state_counts_and_emits_once(api) -> None:
    """Repeat observation through either surface counts the segments exactly once.

    The same terminal state is reported twice by each surface (status poll and
    document listing) and must be accumulated once, with one event. This covers
    the idempotent repeat; the concurrent first observation has its own case
    below, because a serialised repeat cannot exercise the lost-update window.
    """
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.list_documents.return_value = {
        "data": [{"id": "doc-1", "name": "notes.txt", "indexing_status": "completed"}],
        "total": 1,
    }

    with _collect(DOCUMENT_INDEXED_EVENT) as events:
        await _document_status(api, headers)
        listed = await api.client.get(f"{_KB_URL}/kb-1/documents", headers=headers)
        await _document_status(api, headers)
        await api.client.get(f"{_KB_URL}/kb-1/documents", headers=headers)

    assert listed.json()["data"]["items"][0]["status"] == "completed"
    assert (await _kb_detail(api, headers))["chunk_count"] == 5
    assert len([event for event in events if event.get("kb_id") == "kb-1"]) == 1


@pytest.mark.asyncio
async def test_concurrent_first_observations_of_one_completion_count_and_emit_once(api) -> None:
    """Two concurrent first observations of one completion count and emit once.

    Both requests run on their own connection against a committed, still-indexing
    document, so neither can see the other's in-flight transition. Only the
    guarded update can collapse the second observation into a no-op; a
    read-then-write implementation would add the segments twice.
    """
    async with _own_connections(api) as factory:
        tenant, user = await _commit_indexing_document(api, factory)
        headers = api.auth(user, tenant)
        api.console.get_document_indexing_status.side_effect = _concurrent_status_polls(
            {"indexing_status": "completed", "total_segments": 5}
        )
        try:
            with _collect(DOCUMENT_INDEXED_EVENT) as events:
                responses = await asyncio.gather(
                    _document_status(api, headers),
                    _document_status(api, headers),
                )
                detail = await _kb_detail(api, headers)

            assert [response.json()["data"]["status"] for response in responses] == [
                "completed",
                "completed",
            ]
            assert detail["chunk_count"] == 5
            assert len([event for event in events if event.get("kb_id") == "kb-1"]) == 1
        finally:
            await _purge_tenant(factory, tenant)


@pytest.mark.asyncio
async def test_document_listing_records_a_first_observed_completion(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.list_documents.return_value = {
        "data": [{"id": "doc-1", "name": "notes.txt", "indexing_status": "completed"}],
        "total": 1,
    }

    listed = await api.client.get(f"{_KB_URL}/kb-1/documents", headers=headers)

    assert listed.json()["data"]["items"][0]["status"] == "completed"
    assert (await _kb_detail(api, headers))["chunk_count"] == 5


@pytest.mark.asyncio
async def test_delete_unbound_kb_returns_204_and_removes_the_resource(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    headers = api.auth(user, tenant)
    kb_id = await _create_kb(api, headers, "Unbound")

    deleted = await api.client.delete(f"{_KB_URL}/{kb_id}", headers=headers)

    assert deleted.status_code == 204
    api.console.delete_dataset.assert_awaited_once_with("ds-1")
    gone = await api.client.get(f"{_KB_URL}/{kb_id}", headers=headers)
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "KB_NOT_FOUND"


@pytest.mark.asyncio
async def test_delete_bound_kb_returns_409_kb_in_use(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    kb_id = await _create_kb(api, headers, "Bound")
    await _bind_agent(api, headers, kb_id)

    resp = await api.client.delete(f"{_KB_URL}/{kb_id}", headers=headers)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "KB_IN_USE"


@pytest.mark.asyncio
async def test_delete_bound_kb_details_carry_the_binding_agent_list(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    kb_id = await _create_kb(api, headers, "Bound")
    await _bind_agent(api, headers, kb_id)

    resp = await api.client.delete(f"{_KB_URL}/{kb_id}", headers=headers)

    error = resp.json()["error"]
    assert "details" in error
    assert "kb-bound-agent" in json.dumps(error["details"])


@pytest.mark.asyncio
async def test_cascade_delete_unbinds_agents_then_removes_the_kb(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    headers = api.auth(user, tenant)
    kb_id = await _create_kb(api, headers, "Bound")
    await _bind_agent(api, headers, kb_id)

    deleted = await api.client.delete(
        f"{_KB_URL}/{kb_id}", headers=headers, params={"cascade": "true"}
    )

    assert deleted.status_code == 204
    gone = await api.client.get(f"{_KB_URL}/{kb_id}", headers=headers)
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_delete_kb_reports_upstream_failure_as_502_and_keeps_the_record(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    headers = api.auth(user, tenant)
    kb_id = await _create_kb(api, headers, "Upstream Down")
    api.console.delete_dataset.side_effect = DifyConsoleError(500, "{}")

    resp = await api.client.delete(f"{_KB_URL}/{kb_id}", headers=headers)

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "DIFY_ERROR"
    assert (await api.client.get(f"{_KB_URL}/{kb_id}", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_kb_status_is_indexing_while_a_document_is_indexing(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.return_value = {"indexing_status": "indexing"}
    assert (await _document_status(api, headers)).json()["data"]["status"] == "indexing"

    detail = await _kb_detail(api, headers)

    assert detail["indexing_status"] == "indexing"


@pytest.mark.asyncio
async def test_kb_status_is_failed_when_a_document_failed(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.return_value = {
        "indexing_status": "error",
        "error": "boom",
    }
    assert (await _document_status(api, headers)).json()["data"]["status"] == "failed"

    detail = await _kb_detail(api, headers)

    assert detail["indexing_status"] == "failed"


@pytest.mark.asyncio
async def test_kb_status_returns_to_ready_when_its_only_document_completed(api) -> None:
    """C-41 rule three: no indexing and no failed document leaves the KB ready."""
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    assert (await _kb_detail(api, headers))["indexing_status"] == "indexing"
    api.console.get_document_indexing_status.return_value = {
        "indexing_status": "completed",
        "total_segments": 5,
    }
    assert (await _document_status(api, headers)).json()["data"]["status"] == "completed"

    detail = await _kb_detail(api, headers)

    assert detail["indexing_status"] == "ready"


@pytest.mark.asyncio
async def test_kb_status_returns_to_ready_after_its_only_document_is_deleted(api) -> None:
    """C-46 + C-41: dropping the last document row re-folds the aggregate to ready.

    The document is deleted from its failed state, the one C-46 admits besides a
    completed one, so the KB starts out not-ready and the assertion binds to the
    deletion refresh rather than passing on an untouched aggregate.
    """
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.return_value = {
        "indexing_status": "error",
        "error": "boom",
    }
    assert (await _document_status(api, headers)).json()["data"]["status"] == "failed"
    assert (await _kb_detail(api, headers))["indexing_status"] == "failed"

    resp = await api.client.delete(f"{_KB_URL}/kb-1/documents/doc-1", headers=headers)

    assert resp.status_code == 204
    assert (await _kb_detail(api, headers))["indexing_status"] == "ready"


@pytest.mark.asyncio
async def test_recall_while_documents_are_indexing_returns_409_kb_indexing(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.return_value = {"indexing_status": "indexing"}
    assert (await _document_status(api, headers)).json()["data"]["status"] == "indexing"
    calls: list[str] = []
    _use_stub_retriever(api, _retrieve_handler([_record(0.5, "hit")], calls=calls))

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/retrieval-test", headers=headers, json={"query": "anything"}
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "KB_INDEXING"
    assert calls == []


@pytest.mark.asyncio
async def test_recall_while_indexing_is_refused_before_the_missing_dataset_key(api) -> None:
    """C-43: rule two is judged before rule three, so the key is never the answer.

    A knowledge base with no dataset key would be refused with
    ``KB_API_KEY_MISSING`` on its own; while it indexes, the indexing rule has to
    answer first.
    """
    tenant, user = await api.seed(role="knowledge_admin")
    headers = api.auth(user, tenant)
    api.console.get_dataset_api_keys.return_value = {"data": []}
    api.console.create_dataset_api_key.return_value = {"token": ""}
    kb_id = await _create_kb(api, headers, "Keyless")
    await _upload_document(api, headers, kb_id)
    assert (await _kb_detail(api, headers, kb_id))["indexing_status"] == "indexing"

    resp = await api.client.post(
        f"{_KB_URL}/{kb_id}/retrieval-test", headers=headers, json={"query": "anything"}
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "KB_INDEXING"


@pytest.mark.asyncio
async def test_recall_without_a_dataset_api_key_returns_409(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    headers = api.auth(user, tenant)
    api.console.get_dataset_api_keys.return_value = {"data": []}
    api.console.create_dataset_api_key.return_value = {"token": ""}
    kb_id = await _create_kb(api, headers, "Keyless")

    resp = await api.client.post(
        f"{_KB_URL}/{kb_id}/retrieval-test", headers=headers, json={"query": "anything"}
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "KB_API_KEY_MISSING"


@pytest.mark.asyncio
async def test_recall_does_not_create_the_missing_dataset_api_key(api) -> None:
    """C-48: the recall path never creates a dataset key; it only refuses to recall.

    Reading the key on the way to the 409 is compliant, so only the creation is
    asserted here.
    """
    tenant, user = await api.seed(role="knowledge_admin")
    headers = api.auth(user, tenant)
    api.console.get_dataset_api_keys.return_value = {"data": []}
    api.console.create_dataset_api_key.return_value = {"token": ""}
    kb_id = await _create_kb(api, headers, "Keyless")
    creates_before = api.console.create_dataset_api_key.await_count

    resp = await api.client.post(
        f"{_KB_URL}/{kb_id}/retrieval-test", headers=headers, json={"query": "anything"}
    )

    assert resp.status_code == 409
    assert api.console.create_dataset_api_key.await_count == creates_before


@pytest.mark.asyncio
async def test_create_reuses_the_tenants_existing_dataset_api_key(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    headers = api.auth(user, tenant)

    await _create_kb(api, headers, "First")
    await _create_kb(api, headers, "Second")

    assert api.console.get_dataset_api_keys.await_count == 2
    assert api.console.create_dataset_api_key.await_count == 0


@pytest.mark.asyncio
async def test_create_takes_the_tenants_earliest_dataset_api_key(api) -> None:
    """C-48: concurrent key creation converges by taking the earliest one.

    The payload carries the tenant's keys newest first, so picking one by
    position stores a key the tenant did not create first.
    """
    tenant, user = await api.seed(role="knowledge_admin")
    headers = api.auth(user, tenant)
    api.console.get_dataset_api_keys.return_value = {
        "data": [
            {"id": "key-2", "token": "ds-newer", "created_at": "2024-02-01T00:00:00Z"},
            {"id": "key-1", "token": "ds-earliest", "created_at": "2024-01-01T00:00:00Z"},
        ]
    }

    kb_id = await _create_kb(api, headers, "Earliest Key")

    async with api.db.session() as session:
        kb = await session.get(KnowledgeBaseRegistry, kb_id)
        assert kb is not None
        assert kb.dify_api_key == "ds-earliest"


@pytest.mark.asyncio
async def test_create_creates_a_dataset_api_key_when_the_tenant_has_none(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    headers = api.auth(user, tenant)
    api.console.get_dataset_api_keys.return_value = {"data": []}
    api.console.create_dataset_api_key.return_value = {"token": "fresh-dataset-secret"}

    await _create_kb(api, headers, "Creates Key")

    assert api.console.get_dataset_api_keys.await_count == 1
    api.console.create_dataset_api_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_recall_citations_are_ordered_by_descending_score(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    # Dify does not promise an order, so recall has to impose the ranking.
    _use_stub_retriever(
        api, _retrieve_handler([_record(0.42, "low"), _record(0.91, "high"), _record(0.7, "mid")])
    )

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/retrieval-test", headers=headers, json={"query": "ranked"}
    )

    assert resp.status_code == 200
    scores = [citation["score"] for citation in resp.json()["data"]["citations"]]
    assert scores == [0.91, 0.7, 0.42]


@pytest.mark.asyncio
async def test_recall_score_is_the_highest_hit_score(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    _use_stub_retriever(
        api, _retrieve_handler([_record(0.42, "low"), _record(0.91, "high"), _record(0.7, "mid")])
    )

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/retrieval-test", headers=headers, json={"query": "ranked"}
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["score"] == 0.91
    assert body["query"] == "ranked"
    assert len(body["citations"]) == 3


@pytest.mark.asyncio
async def test_recall_without_hits_returns_empty_citations_and_zero_score(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    _use_stub_retriever(api, _retrieve_handler([]))

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/retrieval-test", headers=headers, json={"query": "no hits"}
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["citations"] == []
    assert body["score"] == 0.0


@pytest.mark.asyncio
async def test_recall_reports_the_real_elapsed_latency_in_the_contract_field(api) -> None:
    """C-43: a zero-hit recall still reports the measured end-to-end latency.

    The knowledge-base domain names fields snake_case (C-11), and the value has
    to track the actual round trip — a constant, or a camelCase name, satisfies
    neither half of the clause.
    """
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    _use_stub_retriever(api, _retrieve_handler([], delay=_STUB_UPSTREAM_DELAY_S))

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/retrieval-test", headers=headers, json={"query": "no hits"}
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert "latencyMs" not in body
    latency_ms = body["latency_ms"]
    assert isinstance(latency_ms, float)
    # The upstream stub cannot answer faster than it was told to, so the reported
    # figure has to be the real elapsed time rather than a constant; the 10% slack
    # absorbs clock rounding between the two measurements.
    assert latency_ms >= _STUB_UPSTREAM_DELAY_S * 1000 * 0.9


@pytest.mark.asyncio
async def test_recall_reports_upstream_failure_as_502_dify_error(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    _use_stub_retriever(api, _retrieve_handler(status_code=500))

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/retrieval-test", headers=headers, json={"query": "anything"}
    )

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "DIFY_ERROR"


@pytest.mark.asyncio
async def test_recall_of_an_unknown_kb_returns_404(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    calls: list[str] = []
    _use_stub_retriever(api, _retrieve_handler([_record(0.5, "hit")], calls=calls))

    resp = await api.client.post(
        f"{_KB_URL}/kb-unknown/retrieval-test", headers=headers, json={"query": "anything"}
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "KB_NOT_FOUND"
    assert calls == []


@pytest.mark.asyncio
async def test_a_foreign_tenants_kb_is_never_found(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    other_tenant, _ = await api.seed(role="agent_admin", suffix="other")
    await api.seed_kb(other_tenant, kb_id="kb-other")
    headers = api.auth(user, tenant)
    calls: list[tuple[str, str, dict[str, Any]]] = [
        ("GET", f"{_KB_URL}/kb-other", {}),
        ("DELETE", f"{_KB_URL}/kb-other", {}),
        ("GET", f"{_KB_URL}/kb-other/documents", {}),
        ("GET", f"{_KB_URL}/kb-other/documents/doc-1/status", {}),
        ("POST", f"{_KB_URL}/kb-other/documents/doc-1/retry", {}),
        ("DELETE", f"{_KB_URL}/kb-other/documents/doc-1", {}),
        (
            "POST",
            f"{_KB_URL}/kb-other/documents",
            {"files": {"file": ("notes.txt", b"payload", "text/plain")}},
        ),
        ("POST", f"{_KB_URL}/kb-other/retrieval-test", {"json": {"query": "anything"}}),
    ]

    responses: dict[tuple[str, str], httpx.Response] = {}
    for method, path, kwargs in calls:
        responses[(method, path)] = await api.client.request(
            method, path, headers=headers, **kwargs
        )

    # Every route above is distinct; a path-only key would drop the two reads
    # ("GET /kb-other" and "GET /kb-other/documents") whose DELETE/POST siblings
    # share their path, leaving the most sensitive leaks unasserted.
    assert len(responses) == len(calls)

    for (method, path), response in responses.items():
        assert response.status_code == 404, (method, path)
        assert response.json()["error"]["code"] == "KB_NOT_FOUND", (method, path)


@pytest.mark.asyncio
async def test_kb_listing_is_scoped_to_the_tenant(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin", suffix="a")
    other_tenant, other_user = await api.seed(role="knowledge_admin", suffix="b")
    mine = await _create_kb(api, api.auth(user, tenant), "Mine")
    theirs = await _create_kb(api, api.auth(other_user, other_tenant), "Theirs")

    listed = await api.client.get(_KB_URL, headers=api.auth(user, tenant))
    other_listed = await api.client.get(_KB_URL, headers=api.auth(other_user, other_tenant))

    assert [item["kb_id"] for item in listed.json()["data"]["items"]] == [mine]
    assert [item["kb_id"] for item in other_listed.json()["data"]["items"]] == [theirs]


@pytest.mark.asyncio
async def test_retry_rejects_a_document_that_did_not_fail(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    assert (await _document_status(api, headers)).json()["data"]["status"] == "completed"

    resp = await api.client.post(f"{_KB_URL}/kb-1/documents/doc-1/retry", headers=headers)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_STATE"
    api.console.retry_document_indexing.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_restarts_indexing_for_a_failed_document(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.return_value = {
        "indexing_status": "error",
        "error": "boom",
    }
    assert (await _document_status(api, headers)).json()["data"]["status"] == "failed"

    resp = await api.client.post(f"{_KB_URL}/kb-1/documents/doc-1/retry", headers=headers)

    assert resp.status_code == 204
    api.console.retry_document_indexing.assert_awaited_once_with("ds-kb-1", ["doc-1"])


@pytest.mark.asyncio
async def test_retry_returns_a_failed_document_to_indexing(api) -> None:
    """C-45: a retry clears the failed state, so the document is indexing again."""
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.return_value = {
        "indexing_status": "error",
        "error": "boom",
    }
    assert (await _document_status(api, headers)).json()["data"]["status"] == "failed"
    assert await _stored_document_status(api) == "failed"

    resp = await api.client.post(f"{_KB_URL}/kb-1/documents/doc-1/retry", headers=headers)

    assert resp.status_code == 204
    assert await _stored_document_status(api) == "indexing"


@pytest.mark.asyncio
async def test_a_retried_document_reads_as_indexing_on_both_read_paths(api) -> None:
    """C-45: while Dify re-indexes, both reads answer the retried document as indexing.

    A retry that only restarts upstream leaves the recorded ``failed`` answering
    over the whole re-index — on the status endpoint and on the listing the UI
    polls (C-42), which stops polling on a terminal row.
    """
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.return_value = {
        "indexing_status": "error",
        "error": "boom",
    }
    assert (await _document_status(api, headers)).json()["data"]["status"] == "failed"

    resp = await api.client.post(f"{_KB_URL}/kb-1/documents/doc-1/retry", headers=headers)
    assert resp.status_code == 204

    api.console.get_document_indexing_status.return_value = {"indexing_status": "waiting"}
    api.console.list_documents.return_value = {
        "data": [{"id": "doc-1", "name": "notes.txt", "indexing_status": "waiting"}],
        "total": 1,
    }
    status = (await _document_status(api, headers)).json()["data"]
    listed = await api.client.get(f"{_KB_URL}/kb-1/documents", headers=headers)

    assert status == {"id": "doc-1", "status": "indexing", "error": None}
    assert listed.json()["data"]["items"] == [
        {"id": "doc-1", "name": "notes.txt", "status": "indexing"}
    ]


@pytest.mark.asyncio
async def test_a_retried_document_counts_its_completion_once(api) -> None:
    """C-40: the completion that follows a retry is counted once, not per attempt.

    The failure that preceded the retry contributed no segments, so the KB total
    holds the single set the eventually-completed document carries.
    """
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.get_document_indexing_status.return_value = {
        "indexing_status": "error",
        "error": "boom",
    }
    assert (await _document_status(api, headers)).json()["data"]["status"] == "failed"

    with _collect(DOCUMENT_INDEXED_EVENT) as events:
        retried = await api.client.post(f"{_KB_URL}/kb-1/documents/doc-1/retry", headers=headers)
        assert retried.status_code == 204
        api.console.get_document_indexing_status.return_value = {
            "indexing_status": "completed",
            "total_segments": 5,
        }
        assert (await _document_status(api, headers)).json()["data"]["status"] == "completed"
        # The completion observed again is the same one, and stays counted once.
        assert (await _document_status(api, headers)).json()["data"]["status"] == "completed"

    assert (await _kb_detail(api, headers))["chunk_count"] == 5
    assert len([event for event in events if event.get("kb_id") == "kb-1"]) == 1


@pytest.mark.asyncio
async def test_a_refused_retry_leaves_a_completed_document_counted_once(api) -> None:
    """C-45/C-40: retrying a document that did not fail must not rewind its record.

    A record pulled back into ``indexing`` re-arms the completion transition, so
    the next observed completion would add the same segments and emit the same
    event a second time, and the KB total would carry the document's segments
    twice while a delete reclaims one set.
    """
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    api.console.list_documents.return_value = {
        "data": [{"id": "doc-1", "name": "notes.txt", "indexing_status": "completed"}],
        "total": 1,
    }

    with _collect(DOCUMENT_INDEXED_EVENT) as events:
        assert (await _document_status(api, headers)).json()["data"]["status"] == "completed"
        refused = await api.client.post(f"{_KB_URL}/kb-1/documents/doc-1/retry", headers=headers)
        listed = await api.client.get(f"{_KB_URL}/kb-1/documents", headers=headers)
        assert (await _document_status(api, headers)).json()["data"]["status"] == "completed"

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "INVALID_STATE"
    api.console.retry_document_indexing.assert_not_awaited()
    assert await _stored_document_status(api) == "completed"
    assert listed.json()["data"]["items"][0]["status"] == "completed"
    detail = await _kb_detail(api, headers)
    assert (detail["doc_count"], detail["chunk_count"]) == (1, 5)
    assert len([event for event in events if event.get("kb_id") == "kb-1"]) == 1


@pytest.mark.asyncio
async def test_delete_document_reclaims_its_document_and_chunk_counts(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    headers = api.auth(user, tenant)
    await _upload_document(api, headers)
    assert (await _document_status(api, headers)).json()["data"]["status"] == "completed"
    counted = await _kb_detail(api, headers)
    assert (counted["doc_count"], counted["chunk_count"]) == (1, 5)

    resp = await api.client.delete(f"{_KB_URL}/kb-1/documents/doc-1", headers=headers)

    assert resp.status_code == 204
    detail = await _kb_detail(api, headers)
    assert (detail["doc_count"], detail["chunk_count"]) == (0, 0)
