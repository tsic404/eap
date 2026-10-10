"""SQLAlchemy repository for knowledge-base registry rows."""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import delete, func, select, update
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

    async def get_by_name_for_tenant(
        self, session: AsyncSession, tenant_id: uuid.UUID, name: str
    ) -> KnowledgeBaseRegistry | None:
        """Fetch a KB by name within ``tenant_id`` (name-uniqueness pre-check)."""
        stmt = select(KnowledgeBaseRegistry).where(
            KnowledgeBaseRegistry.tenant_id == tenant_id,
            KnowledgeBaseRegistry.name == name,
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

    async def get_document(
        self, session: AsyncSession, *, kb_id: str, document_id: str
    ) -> KnowledgeDocument | None:
        """Fetch a tracked document row inside the caller's transaction.

        Callers that must decide from the *recorded* state (rather than Dify's
        live state) read it here, so the decision matches what an earlier
        transition wrote.
        """
        stmt = select(KnowledgeDocument).where(
            KnowledgeDocument.kb_id == kb_id,
            KnowledgeDocument.document_id == document_id,
        )
        return cast(KnowledgeDocument | None, await session.scalar(stmt))

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

    async def add_chunk_count(self, session: AsyncSession, *, kb_id: str, delta: int) -> None:
        """Add ``delta`` segments to the KB's chunk total.

        The addition happens in SQL (``chunk_count = chunk_count + :delta``) so
        concurrent document completions accumulate instead of overwriting each
        other the way a read-modify-write on the ORM attribute would.
        """
        stmt = (
            update(KnowledgeBaseRegistry)
            .where(KnowledgeBaseRegistry.kb_id == kb_id)
            .values(chunk_count=KnowledgeBaseRegistry.chunk_count + delta)
        )
        await session.execute(stmt)

    async def add_document_count(self, session: AsyncSession, *, kb_id: str) -> None:
        """Count one more document on the KB.

        The increment happens in SQL so concurrent uploads accumulate instead of
        overwriting each other the way a read-modify-write on the ORM attribute
        would.
        """
        stmt = (
            update(KnowledgeBaseRegistry)
            .where(KnowledgeBaseRegistry.kb_id == kb_id)
            .values(doc_count=KnowledgeBaseRegistry.doc_count + 1)
        )
        await session.execute(stmt)

    async def release_document_counts(
        self, session: AsyncSession, *, kb_id: str, segments: int
    ) -> None:
        """Take one deleted document out of the KB's document and chunk totals.

        Both counters move in SQL, so a concurrent upload or a sibling document
        completing cannot lose its update. ``doc_count`` is guarded at zero and
        ``chunk_count`` floored through ``greatest`` — the table's non-negative
        check constraint would otherwise reject the decrement.
        """
        await session.execute(
            update(KnowledgeBaseRegistry)
            .where(KnowledgeBaseRegistry.kb_id == kb_id, KnowledgeBaseRegistry.doc_count > 0)
            .values(doc_count=KnowledgeBaseRegistry.doc_count - 1)
        )
        await session.execute(
            update(KnowledgeBaseRegistry)
            .where(KnowledgeBaseRegistry.kb_id == kb_id)
            .values(chunk_count=func.greatest(KnowledgeBaseRegistry.chunk_count - segments, 0))
        )

    async def delete_document(self, session: AsyncSession, *, kb_id: str, document_id: str) -> bool:
        """Delete a tracked document row; ``True`` only when a row was removed."""
        result = await session.execute(
            delete(KnowledgeDocument)
            .where(
                KnowledgeDocument.kb_id == kb_id,
                KnowledgeDocument.document_id == document_id,
            )
            .returning(KnowledgeDocument.document_id)
        )
        return result.scalar_one_or_none() is not None
