"""Multi-tenant isolation helpers (architecture doc §8.3, ADR-004)."""

from __future__ import annotations

import uuid

from sqlalchemy import Select, select


def dify_user_identifier(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Encode the Dify ``user`` parameter as ``{tenant_id}:{user_id}`` (ADR-004).

    The tenant prefix keeps Dify conversation scopes collision-free across
    tenants that might reuse the same local user id.
    """
    return f"{tenant_id}:{user_id}"


def tenant_scoped_select[T](model: type[T], tenant_id: uuid.UUID) -> Select[tuple[T]]:
    """Return ``select(model)`` already constrained to ``tenant_id`` (§8.3).

    Every tenant-owned query MUST flow through this helper (or an equivalent
    explicit ``WHERE tenant_id = :tenant_id``) so a handler can never read
    another tenant's rows by omission.
    """
    return select(model).where(model.tenant_id == tenant_id)  # type: ignore[attr-defined]
