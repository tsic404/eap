"""SQLAlchemy repository for knowledge-base registry rows."""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeBaseRegistry, KnowledgeDocument
from app.schemas.knowledge import DocumentStatus


class KnowledgeRepository:
    """CRUD over ``knowledge_base_registry``, scoped to a tenant."""

    async def create(
        self,
        session: AsyncSession,
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
        )
        session.add(kb)
        await session.flush()
        return kb

    async def get_for_tenant(
        self, session: AsyncSession, kb_id: str, tenant_id: uuid.UUID
    ) -> KnowledgeBaseRegistry | None:
        """Fetch a KB owned by ``tenant_id`` (or ``None`` — no cross-tenant leak)."""
        stmt = select(KnowledgeBaseRegistry).where(
            KnowledgeBaseRegistry.kb_id == kb_id,
            KnowledgeBaseRegistry.tenant_id == tenant_id,
        )
        return cast(KnowledgeBaseRegistry | None, await session.scalar(stmt))

    async def list_for_tenant(
        self, session: AsyncSession, tenant_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[list[KnowledgeBaseRegistry], int]:
        total = await session.scalar(
            select(func.count())
            .select_from(KnowledgeBaseRegistry)
            .where(KnowledgeBaseRegistry.tenant_id == tenant_id)
        )
        stmt = (
            select(KnowledgeBaseRegistry)
            .where(KnowledgeBaseRegistry.tenant_id == tenant_id)
            .order_by(KnowledgeBaseRegistry.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = list((await session.scalars(stmt)).all())
        return rows, int(total or 0)

    async def delete(self, session: AsyncSession, kb: KnowledgeBaseRegistry) -> None:
        await session.delete(kb)

    async def insert_document(
        self, session: AsyncSession, *, kb_id: str, document_id: str, name: str
    ) -> None:
        """Atomically insert a document row at ``indexing`` (upload-time).

        ``ON CONFLICT`` refreshes ``name`` without resetting a terminal status,
        so re-observing an already-indexed document never regresses it.
        """
        stmt = (
            pg_insert(KnowledgeDocument)
            .values(
                kb_id=kb_id,
                document_id=document_id,
                name=name,
                status="indexing",
            )
            .on_conflict_do_update(
                index_elements=[KnowledgeDocument.kb_id, KnowledgeDocument.document_id],
                set_={"name": name},
            )
        )
        await session.execute(stmt)

    async def transition_document_status(
        self,
        session: AsyncSession,
        *,
        kb_id: str,
        document_id: str,
        status: DocumentStatus,
        error: str | None = None,
    ) -> bool:
        """Atomically record a terminal status; ``True`` only on a real transition.

        The conditional ``UPDATE ... WHERE status <> :status RETURNING`` fires only
        when the previous status differed, so concurrent polls cannot double-emit.
        """
        stmt = (
            update(KnowledgeDocument)
            .where(
                KnowledgeDocument.kb_id == kb_id,
                KnowledgeDocument.document_id == document_id,
                KnowledgeDocument.status != status,
            )
            .values(status=status, error=error)
            .returning(KnowledgeDocument.document_id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None
