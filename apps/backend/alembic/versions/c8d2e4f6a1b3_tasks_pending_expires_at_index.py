"""index tasks(expires_at) for the pending-approval expiry scan

Revision ID: c8d2e4f6a1b3
Revises: b7c1d2e3f4a5
Create Date: 2026-10-18 00:00:00.000000

The 15s approval-expiry scan reads ``status = 'pending' AND expires_at <= now()``
and orders by ``expires_at``. ``tasks`` only carried
``ix_tasks_tenant_assignee_status``, whose leading tenant column makes it useless
for an all-tenant scan, and nothing prunes the table — so the scan would degrade
into a full scan plus sort as the table grows. This partial index covers exactly
the rows the scan reads (pending rows carrying a deadline).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d2e4f6a1b3"
down_revision: str | None = "b7c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_tasks_pending_expires_at"
_PREDICATE = "status = 'pending' AND expires_at IS NOT NULL"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "tasks",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text(_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="tasks")
