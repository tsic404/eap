"""Executable acceptance cases for TC-tech-kb-01 (knowledge-base creation and upload validation).

Covers the create contract (server-generated KB id, sole KB type, zero initial counts,
tenant-scoped name uniqueness), the fixed order of the upload validation gates, and the
request-validation / authentication envelopes.

The concurrent same-name clause runs its two attempts as genuinely concurrent requests
on their own connections (see ``_own_connections``): the shared harness transaction
serialises statements, so it cannot express two transactions racing on one name.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import get_session
from app.dify_console import CreateDatasetParams, DifyConsoleError
from app.models.knowledge import KnowledgeBaseRegistry
from app.models.tenant import Tenant
from app.models.user import User

TC_ID = "TC-tech-kb-01"

# C-11 fixes the KB identifier as a 32-character hexadecimal string; it does not
# fix the letter case, so both cases pass.
_HEX32 = re.compile(r"\A[0-9a-fA-F]{32}\Z")

_KB_URL = "/api/knowledge-bases"

# Generous enough that a loaded runner cannot fail the race, short enough that a
# genuinely serialised attempt fails the test instead of hanging it.
_UPSTREAM_BARRIER_TIMEOUT_S = 10.0


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


async def _commit_tenant_and_user(
    api: Any, factory: async_sessionmaker[AsyncSession]
) -> tuple[Tenant, User]:
    """Seed a tenant and a knowledge admin that outlive the committing connection."""
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


def _unique_dataset_names(
    attempts: int = 2,
) -> Callable[[CreateDatasetParams], Awaitable[dict[str, str]]]:
    """Stand in for Dify's dataset-name uniqueness during a concurrent create.

    C-37 expects the in-tenant name conflict to be caught by the pre-check or by
    the upstream insert. The barrier holds every attempt at that boundary until
    all of them have arrived, so the race is real rather than one attempt being
    serialised behind the pre-check that already saw the other's committed row.
    """
    taken: set[str] = set()
    lock = asyncio.Lock()
    barrier = asyncio.Barrier(attempts)

    async def _create(params: CreateDatasetParams) -> dict[str, str]:
        await asyncio.wait_for(barrier.wait(), timeout=_UPSTREAM_BARRIER_TIMEOUT_S)
        async with lock:
            if params.name in taken:
                raise DifyConsoleError(409, '{"message": "dataset name already exists"}')
            taken.add(params.name)
        return {"id": f"ds-{uuid.uuid4().hex[:12]}"}

    return _create


@pytest.mark.asyncio
async def test_create_returns_201_with_hex_id_sole_type_and_zero_counts(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")

    resp = await api.client.post(
        _KB_URL, headers=api.auth(user, tenant), json={"name": "Product Docs"}
    )

    assert resp.status_code == 201
    body = resp.json()["data"]
    # The KB id is a server-generated 32-char hex string, never a client value.
    assert _HEX32.fullmatch(body["kb_id"])
    # "business" is the only knowledge-base type the API exposes today.
    assert body["type"] == "business"
    assert body["indexing_status"] == "ready"
    assert body["doc_count"] == 0
    assert body["chunk_count"] == 0


@pytest.mark.asyncio
async def test_create_duplicate_name_in_tenant_returns_409_kb_name_exists(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    headers = api.auth(user, tenant)
    first = await api.client.post(_KB_URL, headers=headers, json={"name": "Handbook"})
    assert first.status_code == 201

    second = await api.client.post(_KB_URL, headers=headers, json={"name": "Handbook"})

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "KB_NAME_EXISTS"
    listed = await api.client.get(_KB_URL, headers=headers)
    assert listed.json()["data"]["total"] == 1


@pytest.mark.asyncio
async def test_create_same_name_in_another_tenant_returns_201(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin", suffix="a")
    other_tenant, other_user = await api.seed(role="knowledge_admin", suffix="b")
    first = await api.client.post(
        _KB_URL, headers=api.auth(user, tenant), json={"name": "Shared Name"}
    )
    assert first.status_code == 201

    second = await api.client.post(
        _KB_URL, headers=api.auth(other_user, other_tenant), json={"name": "Shared Name"}
    )

    assert second.status_code == 201
    assert second.json()["data"]["kb_id"] != first.json()["data"]["kb_id"]


@pytest.mark.asyncio
async def test_upload_validation_order_prefers_empty_file_over_unsupported_format(api) -> None:
    """Gate ①: an empty payload reports EMPTY_FILE even when the extension is rejected."""
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/documents",
        headers=api.auth(user, tenant),
        files={"file": ("malware.exe", b"", "application/octet-stream")},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "EMPTY_FILE"


@pytest.mark.asyncio
async def test_upload_validation_order_prefers_size_limit_over_unsupported_format(api) -> None:
    """Gate ②: an oversized payload reports FILE_TOO_LARGE before the format gate."""
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    oversized = b"A" * (15 * 1024 * 1024 + 1)

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/documents",
        headers=api.auth(user, tenant),
        files={"file": ("malware.exe", oversized, "application/octet-stream")},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_upload_rejects_magic_byte_mismatch_as_unsupported_format(api) -> None:
    """Gate ④: a whitelisted extension whose content does not match is rejected."""
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/documents",
        headers=api.auth(user, tenant),
        files={"file": ("report.pdf", b"this is not a pdf", "application/pdf")},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNSUPPORTED_FORMAT"


@pytest.mark.asyncio
async def test_upload_reports_upstream_without_document_as_502_dify_upload_failed(api) -> None:
    """Gate ⑤: Dify accepting the file but creating no document is an upstream failure."""
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")
    api.console.create_document.return_value = {"documents": []}

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/documents",
        headers=api.auth(user, tenant),
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "DIFY_UPLOAD_FAILED"


@pytest.mark.asyncio
async def test_create_invalid_field_returns_422_validation_error_with_field_details(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")

    resp = await api.client.post(_KB_URL, headers=api.auth(user, tenant), json={"name": ""})

    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    details = error["details"]
    assert isinstance(details, list) and details
    assert any("name" in entry["loc"] for entry in details)


@pytest.mark.asyncio
async def test_upload_without_file_part_returns_422_validation_error(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")

    resp = await api.client.post(f"{_KB_URL}/kb-1/documents", headers=api.auth(user, tenant))

    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert any("file" in entry["loc"] for entry in error["details"])


@pytest.mark.asyncio
async def test_unreadable_multipart_body_keeps_the_generic_400_answer(api) -> None:
    """A body the parser cannot read stays a 400, never an internal error."""
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/documents",
        headers={
            **api.auth(user, tenant),
            "content-type": "multipart/form-data; boundary=abc",
        },
        content=b"garbage",
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_knowledge_routes_without_a_token_return_401(api) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")

    created = await api.client.post(_KB_URL, json={"name": "Anonymous"})
    uploaded = await api.client.post(
        f"{_KB_URL}/kb-1/documents",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert created.status_code == 401
    assert created.json()["error"]["code"] == "UNAUTHORIZED"
    assert uploaded.status_code == 401
    assert uploaded.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_upload_without_a_token_answers_401_before_the_body_is_parsed(api) -> None:
    """The 401 gate outranks the body: an over-budget payload still answers 401.

    The payload is past the upload budget, so a pipeline that parsed the body
    first would answer 422 FILE_TOO_LARGE instead.
    """
    tenant, _ = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant, kb_id="kb-1")

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/documents",
        files={"file": ("huge.txt", b"A" * (20 * 1024 * 1024), "text/plain")},
    )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
    api.console.upload_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_by_an_employee_answers_403_before_the_body_is_parsed(api) -> None:
    """The role gate outranks the body: an over-budget payload still answers 403."""
    tenant, _ = await api.seed(role="knowledge_admin")
    employee = await api.seed_user(tenant, role="employee")
    await api.seed_kb(tenant, kb_id="kb-1")

    resp = await api.client.post(
        f"{_KB_URL}/kb-1/documents",
        headers=api.auth(employee, tenant),
        files={"file": ("huge.txt", b"A" * (20 * 1024 * 1024), "text/plain")},
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"
    api.console.upload_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_create_with_the_same_name_loses_the_race_with_409(api) -> None:
    """Two concurrent creates of one name: one wins, the other gets 409.

    Each attempt runs on its own connection and transaction, so neither can see
    the other's uncommitted row: the conflict has to be resolved by the pre-check
    or by the upstream dataset insert, never by a silent second row.
    """
    async with _own_connections(api) as factory:
        tenant, user = await _commit_tenant_and_user(api, factory)
        headers = api.auth(user, tenant)
        api.console.create_dataset.side_effect = _unique_dataset_names()
        try:
            responses = await asyncio.gather(
                api.client.post(_KB_URL, headers=headers, json={"name": "Race"}),
                api.client.post(_KB_URL, headers=headers, json={"name": "Race"}),
            )

            assert sorted(response.status_code for response in responses) == [201, 409]
            loser = next(response for response in responses if response.status_code == 409)
            assert loser.json()["error"]["code"] == "KB_NAME_EXISTS"
            listed = await api.client.get(_KB_URL, headers=headers)
            names = [item["name"] for item in listed.json()["data"]["items"]]
            assert names == ["Race"]
        finally:
            await _purge_tenant(factory, tenant)
