"""unique (tenant_id, lower(email)) on users

Revision ID: e5f6a7b8c9d0
Revises: c1d2e3f4a5b6
Create Date: 2026-10-18 00:00:00.000000

One address is one account per tenant whatever its case (C-103, C-114): the
member service folds the address when it looks for a conflict, so the database
has to fold it too, or two concurrent creates of ``Alice@acme.com`` and
``alice@acme.com`` both pass that lookup and land as two rows.

A unique index cannot be written ``NOT VALID``, so the two phases of
``deploy/migrations.md`` are a duplicate census and the index itself, in one
transaction — the census refuses the migration and names the rows instead of
leaving the bare constraint violation the index reports. Repair a reported
tenant/address pair by keeping one row and either deleting or re-addressing the
rest, so every folded address is held once, then re-run ``alembic upgrade head``
(the census lists what is left):

```sql
SELECT tenant_id, lower(email) AS folded_email, count(*), string_agg(email, ', ')
FROM users GROUP BY tenant_id, lower(email) HAVING count(*) > 1;
```
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "uq_users_tenant_email_lower"
_CENSUS = (
    "SELECT tenant_id, lower(email) AS folded_email, count(*) AS accounts,"
    " string_agg(id::text, ', ') AS user_ids"
    " FROM users GROUP BY tenant_id, lower(email)"
    " HAVING count(*) > 1 ORDER BY tenant_id, folded_email"
)


def upgrade() -> None:
    # Phase 1: census the historical case-variant duplicates. The index below
    # would abort on the first one it meets; naming every pair lets the operator
    # repair the data before the second run.
    duplicates = op.get_bind().execute(sa.text(_CENSUS)).fetchall()
    if duplicates:
        reported = "; ".join(
            f"{row.tenant_id} / {row.folded_email}: {row.accounts} rows ({row.user_ids})"
            for row in duplicates
        )
        raise RuntimeError(
            "users carry case-variant duplicates of one address; keep one row per"
            f" tenant and address, then re-run this migration: {reported}"
        )

    # Phase 2: the database enforces the folded uniqueness the service assumes.
    op.create_index(_INDEX_NAME, "users", ["tenant_id", sa.text("lower(email)")], unique=True)


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="users")
