"""SQLAlchemy repository for conversation rows (architecture doc §32.4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation


class ConversationRepository:
    """CRUD over ``conversations``, always scoped to the owning user."""

    async def create(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        agent_id: str,
        agent_name: str | None,
        title: str | None,
    ) -> Conversation:
        conversation = Conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            agent_name=agent_name,
            title=title,
            status="active",
        )
        session.add(conversation)
        await session.flush()
        return conversation

    async def get_for_user(
        self, session: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID
    ) -> Conversation | None:
        """Fetch one non-deleted conversation owned by ``user_id`` (no cross-user leak)."""
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.deleted_at.is_(None),
        )
        return cast(Conversation | None, await session.scalar(stmt))

    async def list_for_user(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        agent_id: str | None,
        status: str,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[Conversation], str | None]:
        """Keyset-paginated listing over ``(created_at, id)`` descending.

        The cursor encodes the last row's ``(created_at, id)``; the next page
        starts strictly after it. ``limit + 1`` rows are fetched so a non-empty
        ``next_cursor`` is emitted only when another page actually exists.
        """
        stmt = select(Conversation).where(
            Conversation.user_id == user_id,
            Conversation.deleted_at.is_(None),
            Conversation.status == status,
        )
        if agent_id is not None:
            stmt = stmt.where(Conversation.agent_id == agent_id)
        if cursor is not None:
            decoded = _decode_cursor(cursor)
            if decoded is None:
                # Malformed cursor: return no rows rather than 500-ing on the
                # comparison below.
                return [], None
            created_before, id_before = decoded
            stmt = stmt.where(
                or_(
                    Conversation.created_at < created_before,
                    and_(
                        Conversation.created_at == created_before,
                        Conversation.id < id_before,
                    ),
                )
            )
        stmt = stmt.order_by(Conversation.created_at.desc(), Conversation.id.desc()).limit(
            limit + 1
        )
        rows = list((await session.scalars(stmt)).all())
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if has_more else None
        return page, next_cursor


def _encode_cursor(created_at: datetime, conversation_id: uuid.UUID) -> str:
    # Microsecond timestamp + hex id; the id part contains no ":" so partition
    # on the first ":" round-trips cleanly.
    return f"{int(created_at.timestamp() * 1_000_000)}:{conversation_id}"


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    timestamp_raw, _, id_raw = cursor.partition(":")
    try:
        created_at = datetime.fromtimestamp(int(timestamp_raw) / 1_000_000, tz=UTC)
        return created_at, uuid.UUID(id_raw)
    except (ValueError, OSError):
        return None
