"""drop agent_registry.visibility (unimplemented strategy field)

Revision ID: b2e7c1f5a3d8
Revises: a1f3c5d7e9b2
Create Date: 2026-09-18 00:00:00.000000

The ``visibility`` column was declared but never enforced — every agent was
written with the ORM default ``department`` and the read API returned a
constant. Remove it so the contract no longer exposes a field callers cannot
set or read meaningfully. Visibility scoping belongs to the user-plaza scope
and can be re-introduced there with defined semantics.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2e7c1f5a3d8"
down_revision: str | None = "a1f3c5d7e9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("agent_registry", "visibility")


def downgrade() -> None:
    op.add_column(
        "agent_registry",
        sa.Column(
            "visibility",
            sa.String(length=50),
            nullable=False,
            server_default="department",
        ),
    )
