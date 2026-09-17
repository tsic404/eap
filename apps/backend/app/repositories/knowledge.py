"""SQLAlchemy repository for knowledge-base registry rows."""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeBaseRegistry


class KnowledgeRepository:
    """CRUD over ``knowledge_base_registry``, scoped to a tenant."""

    async def create(
        self,
        session: AsyncSession,
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
