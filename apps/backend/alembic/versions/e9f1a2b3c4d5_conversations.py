"""add conversations table

Revision ID: e9f1a2b3c4d5
Revises: c4e6f8a0b2d4
Create Date: 2026-09-19 00:00:00.000000

``conversations`` stores the platform-stable conversation id (frontend URLs,
user-scoped listing) plus the lazily-learned Dify conversation id. Message
content stays in Dify; this table only tracks ownership, agent binding, soft
delete, and status.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9f1a2b3c4d5"
down_revision: str | None = "c4e6f8a0b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.String(length=100), nullable=False),
        sa.Column("agent_name", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("dify_conversation_id", sa.String(length=255), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_conversations_status"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["agent_registry.tenant_id", "agent_registry.agent_id"],
            name="fk_conversations_tenant_agent_id_agent_registry",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_conversations_tenant_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
    )
    op.create_index("ix_conversations_user_deleted", "conversations", ["user_id", "deleted_at"])
    op.create_index("ix_conversations_tenant_user", "conversations", ["tenant_id", "user_id"])


def downgrade() -> None:
    op.drop_index("ix_conversations_tenant_user", table_name="conversations")
    op.drop_index("ix_conversations_user_deleted", table_name="conversations")
    op.drop_table("conversations")
