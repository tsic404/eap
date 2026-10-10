"""add kb_chunk_count_snapshots for the chunk-count recompute

Revision ID: b7c1d2e3f4a5
Revises: d3f4a5b6c7e8
Create Date: 2026-10-10 00:00:00.000000

``scripts/recompute_kb_counts.py`` rebuilds knowledge-base ``chunk_count``
totals that documents completing before the accumulator shipped never
incremented. This table keeps each changed KB's previous total, keyed by the
run's snapshot id, so a bad run can be undone with ``--rollback``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1d2e3f4a5"
down_revision: str | None = "d3f4a5b6c7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kb_chunk_count_snapshots",
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("kb_id", sa.String(length=100), nullable=False),
        sa.Column("previous_chunk_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_base_registry.kb_id"],
            name="fk_kb_chunk_count_snapshots_kb_id_knowledge_base_registry",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "kb_id", name="pk_kb_chunk_count_snapshots"),
    )


def downgrade() -> None:
    op.drop_table("kb_chunk_count_snapshots")
