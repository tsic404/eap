"""local account password credentials on users

Revision ID: a4b6c8d0e2f4
Revises: b7c1d2e3f4a5
Create Date: 2026-10-18 00:00:00.000000

Local account password login (ADR-007) keeps the slow hash of an account's
password and whether the next login must change it. SSO accounts simply keep a
NULL hash. The flag is read by the local login response (C-111) and the forced
first-login change (C-113).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4b6c8d0e2f4"
down_revision: str | None = "b7c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "password_hash")
