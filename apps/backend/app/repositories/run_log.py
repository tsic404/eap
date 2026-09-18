"""SQLAlchemy repository for run-log reads (architecture doc §32.6).

The repository is read-only: run-log writes happen in ``RunLogService``, which
owns the message_end extraction and ORM construction. Listing is always scoped
to the tenant — the controller passes ``tenant_id`` from the authenticated
user, and every query filters on it, so no cross-tenant leak is possible.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.models.run_log import RunLog


class RunLogRepository:
    """Tenant-scoped reads over ``run_logs`` and its trace child tables."""

    async def list_for_tenant(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        agent_id: str | None,
        conversation_id: str | None,
        status: str | None,
        from_date: datetime | None,
        to_date: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[RunLog], str | None]:
        """Keyset-paginated listing over ``(created_at, id)`` descending.

        Child rows (steps/citations/tool_calls) are not loaded — they are only
        needed by the detail endpoint, and a list page must not fan out into
        the three trace tables.
        """
        stmt = select(RunLog).where(RunLog.tenant_id == tenant_id)
        if agent_id is not None:
            stmt = stmt.where(RunLog.agent_id == agent_id)
        if conversation_id is not None:
            stmt = stmt.where(RunLog.conversation_id == conversation_id)
        if status is not None:
            stmt = stmt.where(RunLog.status == status)
        if from_date is not None:
            stmt = stmt.where(RunLog.created_at >= from_date)
        if to_date is not None:
            stmt = stmt.where(RunLog.created_at <= to_date)
        if cursor is not None:
            decoded = _decode_cursor(cursor)
            if decoded is None:
                # Malformed cursor: return no rows rather than 500-ing on the
                # comparison below.
                return [], None
            created_before, id_before = decoded
            stmt = stmt.where(
                or_(
                    RunLog.created_at < created_before,
                    and_(
                        RunLog.created_at == created_before,
                        RunLog.id < id_before,
                    ),
                )
            )
        stmt = (
            stmt.order_by(RunLog.created_at.desc(), RunLog.id.desc())
            .options(
                noload(RunLog.steps),
                noload(RunLog.citations),
                noload(RunLog.tool_calls),
            )
            .limit(limit + 1)
        )
        rows = list((await session.scalars(stmt)).all())
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if has_more else None
        return page, next_cursor

    async def get_by_trace_id(
        self, session: AsyncSession, tenant_id: uuid.UUID, trace_id: str
    ) -> RunLog | None:
        """Fetch one run log with its steps/citations/tool_calls, or ``None``.

        The three child relationships are ``lazy="selectin"``, so a single-row
        ``scalar`` load pulls the full three-dimension trace without extra
        round-trips.
        """
        stmt = select(RunLog).where(
            RunLog.tenant_id == tenant_id,
            RunLog.trace_id == trace_id,
        )
        return cast(RunLog | None, await session.scalar(stmt))


def _encode_cursor(created_at: datetime, run_log_id: uuid.UUID) -> str:
    # Microsecond timestamp + hex id; the id part contains no ":" so partition
    # on the first ":" round-trips cleanly.
    return f"{int(created_at.timestamp() * 1_000_000)}:{run_log_id}"


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    timestamp_raw, _, id_raw = cursor.partition(":")
    try:
        created_at = datetime.fromtimestamp(int(timestamp_raw) / 1_000_000, tz=UTC)
        return created_at, uuid.UUID(id_raw)
    except (ValueError, OSError):
        return None
