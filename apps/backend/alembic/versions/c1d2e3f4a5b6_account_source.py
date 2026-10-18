"""account source on users

Revision ID: c1d2e3f4a5b6
Revises: a4b6c8d0e2f4
Create Date: 2026-10-18 00:00:00.000000

Account provenance (ADR-007, C-114): a member is either an SSO account created
by the enterprise login or a local account a platform admin created. The value
is fixed at creation — the check constraints keep the two source values and tie
each source to its subject namespace, so the callback's lookup by subject can
never land on a local account.

Existing rows predate the local creation path, so the only password-bearing
rows are ones written with the reserved ``local:`` subject; everything else is
an SSO account.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "a4b6c8d0e2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "account_source",
            sa.String(length=16),
            server_default=sa.text("'sso'"),
            nullable=False,
        ),
    )
    op.execute(
        "UPDATE users SET account_source = 'local' "
        "WHERE password_hash IS NOT NULL AND sso_sub LIKE 'local:%'"
    )
    op.create_check_constraint(
        "ck_users_account_source", "users", "account_source IN ('local', 'sso')"
    )
    op.create_check_constraint(
        "ck_users_account_source_subject",
        "users",
        "(account_source = 'local' AND sso_sub LIKE 'local:%')"
        " OR (account_source = 'sso' AND sso_sub NOT LIKE 'local:%')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_account_source_subject", "users", type_="check")
    op.drop_constraint("ck_users_account_source", "users", type_="check")
    op.drop_column("users", "account_source")
