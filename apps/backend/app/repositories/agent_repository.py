"""AgentRepository: tenant-scoped SQLAlchemy persistence (§5.3 + §32.2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload, selectinload

from app.models.agent import (
    AgentKnowledgeBinding,
    AgentRegistry,
    AgentToolBinding,
)
from app.models.run_log import RunLog

_RECENT_LOGS_LIMIT = 10


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so a search term matches literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class AgentRepository:
    """Tenant-scoped persistence for ``agent_registry``.

    Every query is constrained to ``tenant_id`` so a caller can never read
    another tenant's rows by omission (§8.3). Soft-deleted rows (``archived_at``
    set) are always excluded.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_many(
        self,
        tenant_id: uuid.UUID,
        *,
        page: int,
        page_size: int,
        status: str | None,
        category: str | None,
        type_: str | None,
        search: str | None,
    ) -> tuple[list[AgentRegistry], int]:
        """Return ``(agents, total)`` for the tenant, filtered and paginated."""
        filters = [
            AgentRegistry.tenant_id == tenant_id,
            AgentRegistry.archived_at.is_(None),
        ]
        if status is not None:
            filters.append(AgentRegistry.status == status)
        if category is not None:
            filters.append(AgentRegistry.category == category)
        if type_ is not None:
            filters.append(AgentRegistry.type == type_)
        if search:
            pattern = f"%{_escape_like(search)}%"
            filters.append(
                or_(
                    AgentRegistry.name.ilike(pattern, escape="\\"),
                    AgentRegistry.description.ilike(pattern, escape="\\"),
                )
            )

        total = int(
            await self._session.scalar(
                select(func.count()).select_from(AgentRegistry).where(*filters)
            )
            or 0
        )

        stmt = (
            select(AgentRegistry)
            .where(*filters)
            .options(
                selectinload(AgentRegistry.knowledge_bindings),
                selectinload(AgentRegistry.tool_bindings),
                noload(AgentRegistry.run_logs),
                noload(AgentRegistry.daily_stats),
            )
            .order_by(AgentRegistry.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        agents = list((await self._session.scalars(stmt)).all())
        return agents, total

    async def find_by_id(
        self, tenant_id: uuid.UUID, agent_id: str
    ) -> AgentRegistry | None:
        """Load one agent with its bindings (names included), or ``None``."""
        stmt = (
            select(AgentRegistry)
            .where(
                AgentRegistry.tenant_id == tenant_id,
                AgentRegistry.agent_id == agent_id,
                AgentRegistry.archived_at.is_(None),
            )
            .options(
                selectinload(AgentRegistry.knowledge_bindings).selectinload(
                    AgentKnowledgeBinding.knowledge
                ),
                selectinload(AgentRegistry.tool_bindings).selectinload(
                    AgentToolBinding.tool
                ),
                noload(AgentRegistry.run_logs),
                noload(AgentRegistry.daily_stats),
            )
            .execution_options(populate_existing=True)
        )
        agent: AgentRegistry | None = await self._session.scalar(stmt)
        return agent

    async def find_recent_logs(
        self, tenant_id: uuid.UUID, agent_id: str, limit: int = _RECENT_LOGS_LIMIT
    ) -> list[RunLog]:
        """Return the agent's most recent run logs, newest first."""
        stmt = (
            select(RunLog)
            .where(RunLog.tenant_id == tenant_id, RunLog.agent_id == agent_id)
            .options(
                noload(RunLog.steps),
                noload(RunLog.citations),
                noload(RunLog.tool_calls),
            )
            .order_by(RunLog.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.scalars(stmt)).all())

    async def create(self, agent: AgentRegistry) -> AgentRegistry:
        self._session.add(agent)
        await self._session.flush()
        # Load server-generated timestamps so the returned row reflects them.
        await self._session.refresh(agent)
        return agent

    async def update_with_version(
        self,
        tenant_id: uuid.UUID,
        agent_id: str,
        version: int,
        **fields: object,
    ) -> AgentRegistry | None:
        """Optimistic-lock update: bump ``version`` only if the row is unchanged.

        Returns the refreshed agent on success, ``None`` when the version no
        longer matches (stale write).
        """
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(AgentRegistry)
                .where(
                    AgentRegistry.tenant_id == tenant_id,
                    AgentRegistry.agent_id == agent_id,
                    AgentRegistry.version == version,
                    AgentRegistry.archived_at.is_(None),
                )
                .values(**fields, version=AgentRegistry.version + 1, updated_at=func.now())
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount == 0:
            return None
        await self._session.flush()
        return await self.find_by_id(tenant_id, agent_id)

    async def soft_delete(self, tenant_id: uuid.UUID, agent_id: str) -> bool:
        """Soft-delete by stamping ``archived_at``; returns whether a row matched."""
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(AgentRegistry)
                .where(
                    AgentRegistry.tenant_id == tenant_id,
                    AgentRegistry.agent_id == agent_id,
                    AgentRegistry.archived_at.is_(None),
                )
                .values(archived_at=datetime.now(UTC), updated_at=func.now())
                .execution_options(synchronize_session=False)
            ),
        )
        return result.rowcount == 1
