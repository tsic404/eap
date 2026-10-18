"""merge the agent-identity and account heads back into one

Revision ID: f3b9d2e6a1c8
Revises: 25500fd6a58f, c7e9b1d3f5a7, e5f6a7b8c9d0
Create Date: 2026-10-18 22:16:08.000000

Three heads reached this tree independently: ``25500fd6a58f`` merged the
local-login and tasks-index branches, ``c7e9b1d3f5a7`` scoped agent identity to
the tenant from ``a4b6c8d0e2f4``, and ``e5f6a7b8c9d0`` added the folded users
index from ``c1d2e3f4a5b6``. ``alembic upgrade head`` refuses a graph with more
than one head, so this merge revision collapses them; each branch's own DDL
still runs in its own revision, in an order the graph leaves unconstrained.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "f3b9d2e6a1c8"
down_revision: str | tuple[str, str, str] | None = (
    "25500fd6a58f",
    "c7e9b1d3f5a7",
    "e5f6a7b8c9d0",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
