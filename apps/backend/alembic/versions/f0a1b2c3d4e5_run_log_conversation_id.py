"""add conversation_id to run_logs

Revision ID: f0a1b2c3d4e5
Revises: e9f1a2b3c4d5
Create Date: 2026-09-19 00:00:00.000000

``run_logs.conversation_id`` stores Dify's ``conversation_id`` from the
``message_end`` event so the run-log listing can filter by conversation. It is
nullable: a run log produced outside a conversation (e.g. a future batch run)
has no conversation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0a1b2c3d4e5"
down_revision: str | None = "e9f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run_logs", sa.Column("conversation_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("run_logs", "conversation_id")
