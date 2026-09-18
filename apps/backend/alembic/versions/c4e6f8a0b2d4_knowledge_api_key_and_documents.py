"""add knowledge base dataset api key and per-document status tracking

Revision ID: c4e6f8a0b2d4
Revises: a1f3c5d7e9b2
Create Date: 2026-09-17 22:00:00.000000

``dify_api_key`` stores the dataset-scoped API key used for retrieval testing
(per-dataset bearer, so multi-knowledge-base retrieval never 401s).
``knowledge_documents`` tracks each document's last observed indexing status so
``document.indexed``/``document.index_failed`` fire once on the transition, not
on every poll.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e6f8a0b2d4"
down_revision: str | None = "b2e7c1f5a3d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: existing rows predate retrieval keys and are re-populated on the
    # next create/retrieval cycle (see KnowledgeService.retrieval_test).
    op.add_column(
        "knowledge_base_registry",
        sa.Column("dify_api_key", sa.Text(), nullable=True),
    )
    op.create_table(
        "knowledge_documents",
        sa.Column("kb_id", sa.String(length=100), nullable=False),
        sa.Column("document_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('indexing', 'completed', 'failed')",
            name="ck_knowledge_documents_status",
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_base_registry.kb_id"],
            name="fk_knowledge_documents_kb_id_knowledge_base_registry",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("kb_id", "document_id", name="pk_knowledge_documents"),
    )


def downgrade() -> None:
    op.drop_table("knowledge_documents")
    op.drop_column("knowledge_base_registry", "dify_api_key")
