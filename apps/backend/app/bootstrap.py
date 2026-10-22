"""Seed the local family's deterministic identities (C-88 / C-117).

Run by the local / QA compose one-shot bootstrap container after the backend
has migrated the schema. It always seeds the mock IdP's tenant and employee
identity; when the local-login capability is enabled it also seeds the first
platform administrator as a local account that must change its password.
Production has no such container, so it never seeds a local account (C-95).
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password
from app.config import Settings, get_settings
from app.core.folding import fold, fold_email_domain
from app.db import async_session_factory
from app.models.tenant import Tenant
from app.models.user import (
    ACCOUNT_SOURCE_LOCAL,
    ACCOUNT_SOURCE_SSO,
    LOCAL_SUBJECT_PREFIX,
    User,
)

TENANT_SLUG = "acme"
TENANT_DOMAIN = "acme.com"
ALICE_SUB = "user-1"
ALICE_EMAIL = "alice@acme.com"
PLATFORM_ADMIN_ROLE = "platform_admin"

log = structlog.get_logger(__name__)


async def seed_local_family(session: AsyncSession, settings: Settings) -> None:
    """Seed the tenant, the employee identity and — when enabled — the first admin."""
    # Same key as the SQL seed this replaced: overlapping bring-ups serialize,
    # so a second run observes the first run's rows instead of racing them.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('eap:seed:mock-idp'))"))

    # Match the fold, not the stored text (C-122): a bring-up that wrote the
    # domain in another case owns the same domain, and a second fold-equal row
    # would answer every login on it with TENANT_AMBIGUOUS.
    tenant = await session.scalar(
        select(Tenant).where(Tenant.sso_domain_folded == fold(TENANT_DOMAIN))
    )
    if tenant is None:
        tenant = Tenant(
            name="Acme",
            slug=TENANT_SLUG,
            sso_domain=TENANT_DOMAIN,
            sso_provider="oidc",
            quota_limit=10000,
            quota_used=0,
            status="active",
        )
        session.add(tenant)
        await session.flush()

    alice = await session.scalar(
        select(User.id).where(User.tenant_id == tenant.id, User.sso_sub == ALICE_SUB)
    )
    if alice is None:
        session.add(
            User(
                tenant_id=tenant.id,
                sso_sub=ALICE_SUB,
                email=ALICE_EMAIL,
                name="Alice",
                role="agent_admin",
                status="active",
                account_source=ACCOUNT_SOURCE_SSO,
            )
        )

    admin_created = False
    if settings.local_login_enabled:
        # C-117 reads platform-wide; the platform has no tenant-creation path
        # and the local family seeds one tenant, so the check is equivalent here.
        admin = await session.scalar(
            select(User.id)
            .where(User.tenant_id == tenant.id, User.role == PLATFORM_ADMIN_ROLE)
            .limit(1)
        )
        if admin is None:
            await _add_local_admin(session, tenant, settings)
            admin_created = True

    await session.commit()
    if admin_created:
        log.info(
            "local_admin_seeded",
            tenant_id=str(tenant.id),
            email=settings.local_admin_email,
        )


async def _add_local_admin(session: AsyncSession, tenant: Tenant, settings: Settings) -> None:
    """Add the first platform administrator, refusing to guess its credential."""
    email = settings.local_admin_email.strip()
    if not email or not settings.local_admin_password:
        raise RuntimeError(
            "LOCAL_ADMIN_EMAIL and LOCAL_ADMIN_PASSWORD are required to seed the "
            "first platform administrator"
        )

    # Local login resolves the tenant by email domain (C-111 ④), so an address
    # outside the seeded tenant's domain would seed an administrator nobody can
    # sign in as — refuse instead, like the other unusable bootstrap inputs.
    if not tenant.sso_domain_folded or fold_email_domain(email) != tenant.sso_domain_folded:
        raise RuntimeError(
            f"LOCAL_ADMIN_EMAIL must be inside the seeded tenant domain "
            f"{tenant.sso_domain!r}, got {email!r}"
        )

    conflict = await session.scalar(
        select(User.id)
        .where(User.tenant_id == tenant.id, User.email_folded == fold(email))
        .limit(1)
    )
    if conflict is not None:
        raise RuntimeError(
            f"{email} already exists in the seeded tenant but is not a platform administrator"
        )

    session.add(
        User(
            tenant_id=tenant.id,
            sso_sub=f"{LOCAL_SUBJECT_PREFIX}{uuid.uuid4()}",
            email=email,
            name="Platform Admin",
            role=PLATFORM_ADMIN_ROLE,
            status="active",
            account_source=ACCOUNT_SOURCE_LOCAL,
            password_hash=hash_password(settings.local_admin_password),
            must_change_password=True,
        )
    )


async def run() -> None:
    settings = get_settings()
    async with async_session_factory() as session:
        await seed_local_family(session, settings)


if __name__ == "__main__":
    asyncio.run(run())
