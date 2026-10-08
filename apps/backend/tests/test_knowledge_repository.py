"""KnowledgeRepository DB-backed tests (real Postgres, UoW cascade path)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.knowledge import KnowledgeBaseRegistry, KnowledgeDocument
from app.models.tenant import Tenant
from app.repositories.knowledge import KnowledgeRepository


@pytest.mark.asyncio
async def test_delete_kb_cascades_documents_via_db(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Deleting a KB must not load ``documents`` (lazy="raise") during the UoW.

    With ``passive_deletes=True`` the FK ``ON DELETE CASCADE`` removes child
    rows; without it SQLAlchemy loads the collection to cascade and raises.
    """
    async with session_factory() as session:
        tenant = Tenant(name="Tenant", slug=f"t-{uuid.uuid4().hex[:8]}", sso_provider="local")
        session.add(tenant)
        await session.flush()
        kb = KnowledgeBaseRegistry(
            kb_id="kb-1",
            tenant_id=tenant.id,
            dify_dataset_id="ds-1",
            name="KB",
            type="business",
        )
        session.add(kb)
        await session.flush()
        session.add(
            KnowledgeDocument(
                kb_id="kb-1",
                document_id="doc-1",
                name="report.pdf",
                status="indexing",
            )
        )
        await session.commit()

    async with session_factory() as session:
        kb = await session.get(KnowledgeBaseRegistry, "kb-1")
        assert kb is not None
        await session.delete(kb)
        await session.commit()

    async with session_factory() as session:
        remaining = (
            await session.scalars(
                select(KnowledgeDocument).where(KnowledgeDocument.kb_id == "kb-1")
            )
        ).all()
        assert remaining == []


@pytest.mark.asyncio
async def test_get_by_name_for_tenant_is_tenant_scoped(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Same name in another tenant must not surface as a duplicate."""
    repo = KnowledgeRepository()
    async with session_factory() as session:
        tenant_a = Tenant(name="A", slug=f"t-{uuid.uuid4().hex[:8]}", sso_provider="local")
        tenant_b = Tenant(name="B", slug=f"t-{uuid.uuid4().hex[:8]}", sso_provider="local")
        session.add_all([tenant_a, tenant_b])
        await session.flush()
        session.add(
            KnowledgeBaseRegistry(
                kb_id="kb-1",
                tenant_id=tenant_a.id,
                dify_dataset_id="ds-1",
                name="KB",
                type="business",
            )
        )
        await session.commit()

        found = await repo.get_by_name_for_tenant(session, tenant_a.id, "KB")
        assert found is not None and found.kb_id == "kb-1"
        assert await repo.get_by_name_for_tenant(session, tenant_b.id, "KB") is None
        assert await repo.get_by_name_for_tenant(session, tenant_a.id, "Other") is None
