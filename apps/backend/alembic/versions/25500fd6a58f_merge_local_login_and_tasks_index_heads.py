"""merge the local-login and tasks-index migration heads

Revision ID: 25500fd6a58f
Revises: a4b6c8d0e2f4, c8d2e4f6a1b3
Create Date: 2026-10-18 20:10:08.295379

Both branches were cut from ``b7c1d2e3f4a5`` independently and landed together,
which left the tree with two heads. This merge revision is a no-op that collapses
them back to one; each branch's own DDL still runs in its own revision.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "25500fd6a58f"
down_revision: str | tuple[str, str] | None = ("a4b6c8d0e2f4", "c8d2e4f6a1b3")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
