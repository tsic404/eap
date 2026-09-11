"""FastAPI dependencies: current user, role guard, active tenant.

These are the authorization layer that sits between the JWT middleware (which
decodes the bearer token into ``request.state`` identity context) and business
handlers. The middleware only *establishes* identity; these dependencies
*enforce* it and load the authoritative ORM rows.

- ``get_current_user``  -> 401 unless an authenticated user exists
- ``require_roles``     -> 403 unless the user's role is allowed
- ``get_active_tenant`` -> 401/403/429 unless the authenticated user may act on
  the resolved tenant and that tenant is active and under quota
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import AppError
from app.models.tenant import Tenant
from app.models.user import User

_PLATFORM_ADMIN_ROLE = "platform_admin"


def _parse_uuid(value: str, code: str) -> uuid.UUID:
    """Parse an identity claim into a UUID, raising 401 when malformed."""
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise AppError(401, code, "Invalid token identity") from None


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """Resolve the authenticated user, returning 401 when absent or unknown."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise AppError(401, "UNAUTHORIZED", "Authentication required")

    user = await session.get(User, _parse_uuid(user_id, "UNAUTHORIZED"))
    if user is None or user.status != "active":
        raise AppError(401, "UNAUTHORIZED", "Invalid or inactive user")

    request.state.user = user
    return user


def require_roles(*roles: str) -> Callable[..., Awaitable[User]]:
    """Dependency factory: admit only the given roles, otherwise 403."""

    async def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise AppError(403, "FORBIDDEN", "Insufficient permissions")
        return user

    return checker


async def get_active_tenant(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Tenant:
    """Resolve the active tenant, enforcing ownership, status and quota (§8.3/§23.3).

    Authentication is mandatory: ``get_current_user`` rejects anonymous callers,
    so the untrusted ``X-Tenant-Id`` header can no longer resolve a tenant on its
    own. A non-platform-admin may only act on their own tenant — any other
    tenant id yields 403 *before* a database lookup, so callers cannot infer a
    tenant's existence. Platform admins may operate cross-tenant.

    Returns 403 ``TENANT_SUSPENDED`` for a non-active tenant and 429
    ``QUOTA_EXCEEDED`` once its quota is exhausted.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise AppError(401, "UNAUTHORIZED", "Missing tenant context")

    resolved_tenant_id = _parse_uuid(tenant_id, "UNAUTHORIZED")

    # Reject before touching the DB so a non-admin cannot distinguish "tenant
    # missing" from "tenant not mine" (no existence oracle). Only the caller's
    # own tenant — or any tenant, for a platform admin — is looked up below.
    if user.role != _PLATFORM_ADMIN_ROLE and resolved_tenant_id != user.tenant_id:
        raise AppError(403, "FORBIDDEN", "Cross-tenant access denied")

    tenant = await session.get(Tenant, resolved_tenant_id)
    if tenant is None:
        raise AppError(404, "NOT_FOUND", "Tenant not found")

    if tenant.status != "active":
        raise AppError(403, "TENANT_SUSPENDED", "Tenant is suspended")
    if tenant.quota_used >= tenant.quota_limit:
        raise AppError(429, "QUOTA_EXCEEDED", "Tenant quota exceeded")

    request.state.tenant = tenant
    return tenant
