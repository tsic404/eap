"""Tenant resolution by email domain, shared by both login paths.

The enterprise identity callback (C-23) and the local login request (C-111 ④)
judge the same way and answer the same codes: only the domain part is folded
(C-122 ①), no match is ``403 TENANT_NOT_FOUND`` and more than one match —
possible because the stored domain is only constrained case-sensitively — is
``403 TENANT_AMBIGUOUS`` rather than a guess at which tenant the caller meant.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthError
from app.core.folding import fold_email_domain
from app.models.tenant import Tenant


async def resolve_tenant_by_email_domain(session: AsyncSession, email: str) -> Tenant:
    """Return the tenant owning ``email``'s domain, or raise the fixed 403s."""
    domain = fold_email_domain(email)
    tenants = (
        (await session.execute(select(Tenant).where(Tenant.sso_domain_folded == domain)))
        .scalars()
        .all()
    )
    if not tenants:
        raise AuthError(403, "TENANT_NOT_FOUND", "No tenant matches this SSO domain")
    if len(tenants) > 1:
        raise AuthError(403, "TENANT_AMBIGUOUS", "Multiple tenants match this SSO domain")
    return tenants[0]
