"""Seed the database with the initial tenant, platform admin, and global tool templates.

Idempotent and self-healing: every required record is checked and inserted
independently, so re-running repairs a partially seeded database, and a
transaction-scoped advisory lock serializes concurrent seed processes.

Run with ``python -m app.seed`` after ``alembic upgrade head``.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_factory
from app.models.tenant import Tenant
from app.models.tool import ToolRegistry
from app.models.user import User

DEFAULT_TENANT_SLUG = "default"
DEFAULT_ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")

# tool_id → constructor kwargs for the two global (tenant-agnostic) tool templates.
GLOBAL_TOOL_TEMPLATES: tuple[tuple[str, dict[str, str | None]], ...] = (
    (
        "tool-template-http",
        {
            "name": "HTTP API Tool",
            "type": "http",
            "risk_level": "medium",
            "permission_mode": "auto",
        },
    ),
    (
        "tool-template-webhook",
        {
            "name": "Webhook Tool",
            "type": "webhook",
            "risk_level": "low",
            "permission_mode": "auto",
        },
    ),
)


async def seed(session: AsyncSession) -> None:
    """Create initial data, inserting any missing record independently."""
    # Serialize concurrent seeders; the lock is released at commit/rollback.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('eap:seed'))"))

    tenant = await session.scalar(select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG))
    if tenant is None:
        tenant = Tenant(
            name="Default Tenant",
            slug=DEFAULT_TENANT_SLUG,
            sso_provider="local",
            status="active",
        )
        session.add(tenant)
        await session.flush()

    admin = await session.scalar(
        select(User).where(User.tenant_id == tenant.id, User.email == DEFAULT_ADMIN_EMAIL)
    )
    if admin is None:
        session.add(
            User(
                tenant_id=tenant.id,
                sso_sub="admin-local",
                email=DEFAULT_ADMIN_EMAIL,
                name="Admin",
                role="platform_admin",
                status="active",
            )
        )

    # Global tool templates are tenant-agnostic (tenant_id IS NULL).
    for tool_id, fields in GLOBAL_TOOL_TEMPLATES:
        exists = await session.scalar(select(ToolRegistry).where(ToolRegistry.tool_id == tool_id))
        if exists is None:
            session.add(ToolRegistry(tool_id=tool_id, tenant_id=None, status="active", **fields))

    await session.commit()


async def run() -> None:
    async with async_session_factory() as session:
        await seed(session)


if __name__ == "__main__":
    asyncio.run(run())
