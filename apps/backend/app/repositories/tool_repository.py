"""ToolRepository: SQLAlchemy persistence for the tool registry.

Tenant isolation (§8.3): every read is scoped to the caller's tenant, with
global tools (``tenant_id IS NULL``) shared across tenants.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import ToolRegistry


class ToolRepository:
    """CRUD repository for ``tool_registry`` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, tool: ToolRegistry) -> ToolRegistry:
        self.session.add(tool)
        await self.session.flush()
        await self.session.refresh(tool)
        return tool

    async def get(self, tool_id: str) -> ToolRegistry | None:
        return await self.session.get(ToolRegistry, tool_id)

    async def list_for_tenant(self, tenant_id: uuid.UUID) -> list[ToolRegistry]:
        statement = (
            select(ToolRegistry)
            .where(
                or_(
                    ToolRegistry.tenant_id == tenant_id,
                    ToolRegistry.tenant_id.is_(None),
                )
            )
            .order_by(ToolRegistry.created_at.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def delete(self, tool: ToolRegistry) -> None:
        await self.session.delete(tool)
        await self.session.flush()
