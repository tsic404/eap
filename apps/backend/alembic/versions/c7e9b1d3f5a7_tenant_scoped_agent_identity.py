"""scope agent identity to the tenant

Revision ID: c7e9b1d3f5a7
Revises: a4b6c8d0e2f4
Create Date: 2026-10-18 00:00:00.000000

``agent_registry`` keyed rows by ``agent_id`` alone, so an identifier was unique
across every tenant even though C-103 scopes agent-id uniqueness to the tenant.
The primary key becomes ``(tenant_id, agent_id)`` and the child tables that
referenced an agent by id alone gain ``tenant_id`` so their foreign keys stay
tenant-consistent: the binding and daily-stat tables get the column (backfilled
from ``agent_registry``), while ``run_logs`` and ``conversations`` — which
already carry it — keep their composite foreign keys, now anchored on the new
primary key instead of the dropped stand-alone unique constraint.

Downgrade restores the global key and therefore fails when two tenants already
hold the same agent identifier: that state is not representable in the old
schema. Delete or rename the duplicates before downgrading.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e9b1d3f5a7"
down_revision: str | None = "a4b6c8d0e2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_SCOPED_TABLES = (
    "agent_knowledge_bindings",
    "agent_tool_bindings",
    "agent_daily_stats",
)

# Every child row's tenant is derived from the parent agent, which was still
# globally keyed when this migration starts, so the backfill is unambiguous.
_BACKFILL_STATEMENTS = (
    "UPDATE agent_knowledge_bindings AS child SET tenant_id = parent.tenant_id "
    "FROM agent_registry AS parent WHERE parent.agent_id = child.agent_id",
    "UPDATE agent_tool_bindings AS child SET tenant_id = parent.tenant_id "
    "FROM agent_registry AS parent WHERE parent.agent_id = child.agent_id",
    "UPDATE agent_daily_stats AS child SET tenant_id = parent.tenant_id "
    "FROM agent_registry AS parent WHERE parent.agent_id = child.agent_id",
)


def upgrade() -> None:
    # Phase 1: carry tenant_id onto the child tables, backfilled from the agent.
    for table in _TENANT_SCOPED_TABLES:
        op.add_column(table, sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    for statement in _BACKFILL_STATEMENTS:
        op.execute(statement)
    for table in _TENANT_SCOPED_TABLES:
        op.alter_column(table, "tenant_id", nullable=False)

    # Phase 2: drop every reference that leans on the global agent id. The two
    # composite keys go too: they are anchored on the unique constraint below.
    op.drop_constraint(
        "fk_agent_knowledge_bindings_agent_id_agent_registry",
        "agent_knowledge_bindings",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_agent_tool_bindings_agent_id_agent_registry",
        "agent_tool_bindings",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_agent_daily_stats_agent_id_agent_registry", "agent_daily_stats", type_="foreignkey"
    )
    op.drop_constraint("fk_run_logs_agent_id_agent_registry", "run_logs", type_="foreignkey")
    op.drop_constraint("fk_run_logs_tenant_agent_id_agent_registry", "run_logs", type_="foreignkey")
    op.drop_constraint(
        "fk_conversations_tenant_agent_id_agent_registry", "conversations", type_="foreignkey"
    )

    # Phase 3: swap the primary keys; the composite agent key is the uniqueness
    # scope, so the stand-alone unique constraint is redundant.
    op.drop_constraint("pk_agent_registry", "agent_registry", type_="primary")
    op.drop_constraint("pk_agent_knowledge_bindings", "agent_knowledge_bindings", type_="primary")
    op.drop_constraint("pk_agent_tool_bindings", "agent_tool_bindings", type_="primary")
    op.drop_constraint("pk_agent_daily_stats", "agent_daily_stats", type_="primary")
    op.drop_constraint("uq_agent_registry_tenant_agent_id", "agent_registry", type_="unique")

    op.create_primary_key("pk_agent_registry", "agent_registry", ["tenant_id", "agent_id"])
    op.create_primary_key(
        "pk_agent_knowledge_bindings",
        "agent_knowledge_bindings",
        ["tenant_id", "agent_id", "kb_id"],
    )
    op.create_primary_key(
        "pk_agent_tool_bindings",
        "agent_tool_bindings",
        ["tenant_id", "agent_id", "tool_id"],
    )
    op.create_primary_key(
        "pk_agent_daily_stats", "agent_daily_stats", ["tenant_id", "agent_id", "stat_date"]
    )

    # Phase 4: rebuild the agent references against the tenant-scoped key.
    op.create_foreign_key(
        "fk_agent_knowledge_bindings_tenant_agent_id_agent_registry",
        "agent_knowledge_bindings",
        "agent_registry",
        ["tenant_id", "agent_id"],
        ["tenant_id", "agent_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_agent_tool_bindings_tenant_agent_id_agent_registry",
        "agent_tool_bindings",
        "agent_registry",
        ["tenant_id", "agent_id"],
        ["tenant_id", "agent_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_agent_daily_stats_tenant_agent_id_agent_registry",
        "agent_daily_stats",
        "agent_registry",
        ["tenant_id", "agent_id"],
        ["tenant_id", "agent_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_run_logs_tenant_agent_id_agent_registry",
        "run_logs",
        "agent_registry",
        ["tenant_id", "agent_id"],
        ["tenant_id", "agent_id"],
    )
    op.create_foreign_key(
        "fk_conversations_tenant_agent_id_agent_registry",
        "conversations",
        "agent_registry",
        ["tenant_id", "agent_id"],
        ["tenant_id", "agent_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_knowledge_bindings_tenant_agent_id_agent_registry",
        "agent_knowledge_bindings",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_agent_tool_bindings_tenant_agent_id_agent_registry",
        "agent_tool_bindings",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_agent_daily_stats_tenant_agent_id_agent_registry",
        "agent_daily_stats",
        type_="foreignkey",
    )
    op.drop_constraint("fk_run_logs_tenant_agent_id_agent_registry", "run_logs", type_="foreignkey")
    op.drop_constraint(
        "fk_conversations_tenant_agent_id_agent_registry", "conversations", type_="foreignkey"
    )

    op.drop_constraint("pk_agent_registry", "agent_registry", type_="primary")
    op.drop_constraint("pk_agent_knowledge_bindings", "agent_knowledge_bindings", type_="primary")
    op.drop_constraint("pk_agent_tool_bindings", "agent_tool_bindings", type_="primary")
    op.drop_constraint("pk_agent_daily_stats", "agent_daily_stats", type_="primary")

    # The old key is global: this fails if two tenants share an identifier.
    op.create_primary_key("pk_agent_registry", "agent_registry", ["agent_id"])
    op.create_primary_key(
        "pk_agent_knowledge_bindings", "agent_knowledge_bindings", ["agent_id", "kb_id"]
    )
    op.create_primary_key("pk_agent_tool_bindings", "agent_tool_bindings", ["agent_id", "tool_id"])
    op.create_primary_key("pk_agent_daily_stats", "agent_daily_stats", ["agent_id", "stat_date"])
    op.create_unique_constraint(
        "uq_agent_registry_tenant_agent_id", "agent_registry", ["tenant_id", "agent_id"]
    )

    op.create_foreign_key(
        "fk_agent_knowledge_bindings_agent_id_agent_registry",
        "agent_knowledge_bindings",
        "agent_registry",
        ["agent_id"],
        ["agent_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_agent_tool_bindings_agent_id_agent_registry",
        "agent_tool_bindings",
        "agent_registry",
        ["agent_id"],
        ["agent_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_agent_daily_stats_agent_id_agent_registry",
        "agent_daily_stats",
        "agent_registry",
        ["agent_id"],
        ["agent_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_run_logs_agent_id_agent_registry",
        "run_logs",
        "agent_registry",
        ["agent_id"],
        ["agent_id"],
    )
    op.create_foreign_key(
        "fk_run_logs_tenant_agent_id_agent_registry",
        "run_logs",
        "agent_registry",
        ["tenant_id", "agent_id"],
        ["tenant_id", "agent_id"],
    )
    op.create_foreign_key(
        "fk_conversations_tenant_agent_id_agent_registry",
        "conversations",
        "agent_registry",
        ["tenant_id", "agent_id"],
        ["tenant_id", "agent_id"],
    )

    for table in _TENANT_SCOPED_TABLES:
        op.drop_column(table, "tenant_id")
