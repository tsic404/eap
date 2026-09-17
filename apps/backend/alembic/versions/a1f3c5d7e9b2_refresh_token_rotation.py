"""add refresh token rotation columns (family_id, revoked_reason)

Revision ID: a1f3c5d7e9b2
Revises: 9c3a1f2b4d5e
Create Date: 2026-09-17 12:00:00.000000

Adds the two columns refresh-token rotation needs (architecture §34.1):
``family_id`` groups tokens minted from one login chain so a replay can revoke
the whole family; ``revoked_reason`` distinguishes a normal rotation (grace
window applies) from logout/reuse revocation (immediate reject).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f3c5d7e9b2"
down_revision: str | None = "9c3a1f2b4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable add first so existing rows survive; each pre-rotation token gets
    # its own family (gen_random_uuid comes from the pgcrypto extension enabled
    # in the initial schema migration).
    op.add_column(
        "refresh_tokens",
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "refresh_tokens",
        sa.Column("revoked_reason", sa.String(length=50), nullable=True),
    )
    op.execute("UPDATE refresh_tokens SET family_id = gen_random_uuid() WHERE family_id IS NULL")
    op.alter_column("refresh_tokens", "family_id", nullable=False)
    op.create_index(
        "ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_column("refresh_tokens", "revoked_reason")
    op.drop_column("refresh_tokens", "family_id")
