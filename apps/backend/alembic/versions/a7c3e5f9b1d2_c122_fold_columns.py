"""C-122 fold columns for tenant domains and account addresses

Revision ID: a7c3e5f9b1d2
Revises: f3b9d2e6a1c8
Create Date: 2026-10-22 00:00:00.000000

Tenant resolution and account uniqueness compare folded values (C-122). The
fold is the Unicode default full lowercase mapping: PostgreSQL ``lower()`` only
approximates it and its result follows the database collation, so the key is
computed in the application and stored beside the original text. The database
only tests equality on those keys, and both columns are declared ``COLLATE "C"``
so that test is code-point equality whatever the database default or the
connection's collation is. Their width exceeds the text they fold because the
fold can expand (``İ`` becomes two code points), so a stored value always fits.

A unique index cannot be written ``NOT VALID``, so the fold collision census
runs before the index: it names every tenant/address group that already folds
together instead of leaving the bare constraint violation. Repair a reported
group by keeping one row per tenant and folded address, then re-run
``alembic upgrade head`` (the census lists what is left). Downgrading restores
the pre-C-122 comparison key, which folds together addresses this migration
keeps apart, so the downgrade runs the mirror-image census first and aborts
with the groups rather than on a bare unique violation.

```sql
SELECT tenant_id, email_folded, count(*), string_agg(email, ', ')
FROM users GROUP BY tenant_id, email_folded HAVING count(*) > 1;
```
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.folding import fold

# revision identifiers, used by Alembic.
revision: str = "a7c3e5f9b1d2"
down_revision: str | None = "f3b9d2e6a1c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_EMAIL_INDEX = "uq_users_tenant_email_lower"
_NEW_EMAIL_INDEX = "uq_users_tenant_email_folded"
_DOMAIN_INDEX = "ix_tenants_sso_domain_folded"
_NEW_KEY_CENSUS = (
    "SELECT tenant_id, email_folded AS folded, count(*) AS accounts,"
    " string_agg(id::text, ', ') AS user_ids"
    " FROM users GROUP BY tenant_id, email_folded"
    " HAVING count(*) > 1 ORDER BY tenant_id, email_folded"
)
_OLD_KEY_CENSUS = (
    "SELECT tenant_id, lower(email) AS folded, count(*) AS accounts,"
    " string_agg(id::text, ', ') AS user_ids"
    " FROM users GROUP BY tenant_id, lower(email)"
    " HAVING count(*) > 1 ORDER BY tenant_id, lower(email)"
)


def _collisions(connection: sa.Connection, census: str) -> str:
    """Name every tenant/address group the census finds; empty when there is none."""
    rows = connection.execute(sa.text(census)).fetchall()
    return "; ".join(
        f"{row.tenant_id} / {row.folded}: {row.accounts} rows ({row.user_ids})" for row in rows
    )


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("sso_domain_folded", sa.String(512, collation="C"), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("email_folded", sa.String(512, collation="C"), nullable=True),
    )

    connection = op.get_bind()
    domains = connection.execute(sa.text("SELECT id, sso_domain FROM tenants")).fetchall()
    for tenant_id, domain in domains:
        connection.execute(
            sa.text("UPDATE tenants SET sso_domain_folded = :folded WHERE id = :id"),
            {"folded": fold(domain) if domain is not None else None, "id": tenant_id},
        )
    addresses = connection.execute(sa.text("SELECT id, email FROM users")).fetchall()
    for user_id, email in addresses:
        connection.execute(
            sa.text("UPDATE users SET email_folded = :folded WHERE id = :id"),
            {"folded": fold(email), "id": user_id},
        )

    collisions = _collisions(connection, _NEW_KEY_CENSUS)
    if collisions:
        raise RuntimeError(
            "users carry address groups that fold together; keep one row per tenant"
            f" and folded address, then re-run this migration: {collisions}"
        )

    op.alter_column("users", "email_folded", nullable=False)
    op.drop_index(_OLD_EMAIL_INDEX, table_name="users")
    op.create_index(_NEW_EMAIL_INDEX, "users", ["tenant_id", "email_folded"], unique=True)
    op.create_index(_DOMAIN_INDEX, "tenants", ["sso_domain_folded"])


def downgrade() -> None:
    connection = op.get_bind()
    # The pre-C-122 key folds together addresses C-122 keeps apart (PostgreSQL
    # ``lower()`` drops the combining dot of ``İ``), so rows this migration
    # allowed cannot always carry the old unique index; name them instead.
    collisions = _collisions(connection, _OLD_KEY_CENSUS)
    if collisions:
        raise RuntimeError(
            "addresses that C-122 keeps apart fold together under the pre-C-122 key;"
            f" keep one row per group before downgrading: {collisions}"
        )
    op.drop_index(_DOMAIN_INDEX, table_name="tenants")
    op.drop_index(_NEW_EMAIL_INDEX, table_name="users")
    op.create_index(_OLD_EMAIL_INDEX, "users", ["tenant_id", sa.text("lower(email)")], unique=True)
    op.drop_column("users", "email_folded")
    op.drop_column("tenants", "sso_domain_folded")
