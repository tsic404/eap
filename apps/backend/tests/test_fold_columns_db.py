"""Real-DB tests for the C-122 comparison keys.

The fold operator is unit-testable without a database, but two properties of the
keys it feeds are only observable in PostgreSQL: the collation their equality
test runs under, and the room they leave for the fold to expand.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import noload

from app.core.folding import fold
from app.models.tenant import Tenant
from app.models.user import User

_COLUMN_COLLATION = text(
    "SELECT c.collname, c.collisdeterministic"
    " FROM pg_attribute a"
    " JOIN pg_class t ON t.oid = a.attrelid"
    " JOIN pg_collation c ON c.oid = a.attcollation"
    " WHERE t.relname = :table AND a.attname = :column"
)
_INDEX_COLLATION = text(
    "SELECT c.collname"
    " FROM pg_index i"
    " CROSS JOIN LATERAL unnest("
    " string_to_array(i.indkey::text, ' ')::int2[],"
    " string_to_array(i.indcollation::text, ' ')::int2[]"
    " ) AS k(attnum, coll)"
    " JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum"
    " JOIN pg_collation c ON c.oid = k.coll"
    " WHERE i.indexrelid = to_regclass(:index) AND a.attname = :column"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("table", "column"),
    [("users", "email_folded"), ("tenants", "sso_domain_folded")],
)
async def test_fold_column_compares_by_code_point(session_factory, table: str, column: str) -> None:  # type: ignore[no-untyped-def]
    """No database or connection collation may decide which folds are equal."""
    async with session_factory() as session:
        row = (
            await session.execute(_COLUMN_COLLATION, {"table": table, "column": column})
        ).one_or_none()
    assert row is not None, (table, column)
    assert (row.collname, row.collisdeterministic) == ("C", True)


@pytest.mark.asyncio
async def test_unique_address_index_keeps_the_column_collation(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The uniqueness match is the same code-point test as the lookup."""
    async with session_factory() as session:
        collations = (
            (
                await session.execute(
                    _INDEX_COLLATION,
                    {"index": "uq_users_tenant_email_folded", "column": "email_folded"},
                )
            )
            .scalars()
            .all()
        )
    assert collations == ["C"]


@pytest.mark.asyncio
async def test_fold_keys_hold_values_that_expand_when_folded(session_factory) -> None:  # type: ignore[no-untyped-def]
    """``İ`` folds to two code points, so a long domain or address still fits its key."""
    domain = "İ" * 130 + ".de"
    address = "İ" * 130 + "@example.com"
    assert len(fold(domain)) > 255
    assert len(fold(address)) > 255

    async with session_factory() as session:
        tenant = Tenant(name="Fold", slug=f"fold-{uuid.uuid4().hex[:8]}", sso_domain=domain)
        session.add(tenant)
        await session.flush()
        session.add(
            User(
                tenant_id=tenant.id,
                sso_sub="sub-fold",
                email=address,
                name="Fold",
            )
        )
        await session.commit()
        tenant_id = tenant.id

    async with session_factory() as session:
        stored_user = await session.scalar(
            select(User)
            .where(User.tenant_id == tenant_id, User.email_folded == fold(address))
            .options(noload("*"))
        )
        stored_tenant = await session.scalar(
            select(Tenant).where(Tenant.sso_domain_folded == fold(domain)).options(noload("*"))
        )

    assert stored_user is not None and stored_user.email == address
    assert stored_tenant is not None and stored_tenant.sso_domain == domain
