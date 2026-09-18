"""Real-DB tests for the atomic document-tracking SQL.

The service unit tests mock ``KnowledgeRepository``, so the PostgreSQL-specific
SQL — ``INSERT ... ON CONFLICT DO UPDATE`` and the conditional
``UPDATE ... WHERE status <> :status RETURNING`` — is never executed there.
These tests exercise that SQL against a real database.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.knowledge import KnowledgeBaseRegistry, KnowledgeDocument
from app.models.tenant import Tenant
from app.repositories.knowledge import KnowledgeRepository


async def _seed_kb(session_factory) -> uuid.UUID:  # type: ignore[no-untyped-def]
    tenant_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name="Tenant",
                slug=f"t-{uuid.uuid4().hex[:8]}",
                sso_provider="local",
            )
        )
        await session.flush()
        session.add(
            KnowledgeBaseRegistry(
                kb_id="kb-1",
                tenant_id=tenant_id,
                dify_dataset_id="ds-1",
                name="KB",
                type="business",
            )
        )
        await session.commit()
    return tenant_id


@pytest.mark.asyncio
async def test_insert_document_does_not_reset_terminal_status(session_factory) -> None:  # type: ignore[no-untyped-def]
    await _seed_kb(session_factory)
    repo = KnowledgeRepository()

    async with session_factory() as session:
        await repo.insert_document(session, kb_id="kb-1", document_id="doc-1", name="a.pdf")
        await session.commit()

        assert await repo.transition_document_status(
            session, kb_id="kb-1", document_id="doc-1", status="completed"
        )
        await session.commit()

        # Re-insert via ON CONFLICT DO UPDATE must not regress a terminal status.
        await repo.insert_document(session, kb_id="kb-1", document_id="doc-1", name="a.pdf")
        await session.commit()

        doc = await session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.kb_id == "kb-1",
                KnowledgeDocument.document_id == "doc-1",
            )
        )
        assert doc is not None
        assert doc.status == "completed"


@pytest.mark.asyncio
async def test_transition_document_status_is_idempotent(session_factory) -> None:  # type: ignore[no-untyped-def]
    await _seed_kb(session_factory)
    repo = KnowledgeRepository()

    async with session_factory() as session:
        await repo.insert_document(session, kb_id="kb-1", document_id="doc-1", name="a.pdf")
        await session.commit()

        # indexing -> completed: a real transition.
        assert await repo.transition_document_status(
            session, kb_id="kb-1", document_id="doc-1", status="completed"
        )
        await session.commit()

        # completed -> completed: no transition.
        assert not await repo.transition_document_status(
            session, kb_id="kb-1", document_id="doc-1", status="completed"
        )


@pytest.mark.asyncio
async def test_transition_document_status_missing_row_returns_false(session_factory) -> None:  # type: ignore[no-untyped-def]
    await _seed_kb(session_factory)
    repo = KnowledgeRepository()

    async with session_factory() as session:
        assert not await repo.transition_document_status(
            session, kb_id="kb-1", document_id="nope", status="completed"
        )
