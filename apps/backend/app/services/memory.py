"""Minimal memory recall for conversation inputs (architecture doc §32.4.1).

P1 has no semantic retrieval (that is the P2 memory-extraction pipeline); recall
here returns the user's highest-importance stored facts/preferences so the
conversation adapter can inject them into Dify ``inputs``. The adapter consumes
the narrow ``MemoryRecall`` protocol, so this service is the only concrete
implementation it needs.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_memory import UserMemory

_MAX_RECALLED_MEMORIES = 20


class MemoryService:
    """Reads the user's stored memories for prompt injection."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def recall(
        self,
        user_id: str,
        tenant_id: str,
        query: str,
    ) -> list[dict[str, Any]]:
        """Return ``[{type, content}]`` for the user's top memories, most important first.

        ``query`` is accepted for protocol parity with the future semantic
        recall; the P1 implementation does not rank by it.
        """
        try:
            user_uuid = uuid.UUID(user_id)
            tenant_uuid = uuid.UUID(tenant_id)
        except (ValueError, TypeError, AttributeError):
            return []

        stmt = (
            select(UserMemory)
            .where(
                UserMemory.user_id == user_uuid,
                UserMemory.tenant_id == tenant_uuid,
                # Expired memories must never reach the model prompt: a stale
                # fact/preference is prompt-injection surface (§32.4.1).
                or_(
                    UserMemory.expires_at.is_(None),
                    UserMemory.expires_at > func.now(),
                ),
            )
            .order_by(UserMemory.importance.desc(), UserMemory.updated_at.desc())
            .limit(_MAX_RECALLED_MEMORIES)
        )
        rows = (await self._session.scalars(stmt)).all()
        return [{"type": row.type, "content": row.content} for row in rows]
