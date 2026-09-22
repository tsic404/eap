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

    async def get_by_name(self, name: str, tenant_id: uuid.UUID) -> ToolRegistry | None:
        """Return a tenant-visible tool by display name, tenant-scoped preferred.

        Dify's ``agent_thought.tool`` carries the tool's display name; a tenant
        may have a tenant-scoped and a global tool sharing a name, so the
        tenant-scoped row (``tenant_id IS NOT NULL``) wins.
        """
        statement = (
            select(ToolRegistry)
            .where(
                ToolRegistry.name == name,
                or_(
                    ToolRegistry.tenant_id == tenant_id,
                    ToolRegistry.tenant_id.is_(None),
                ),
            )
            .order_by(ToolRegistry.tenant_id.is_(None), ToolRegistry.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

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
