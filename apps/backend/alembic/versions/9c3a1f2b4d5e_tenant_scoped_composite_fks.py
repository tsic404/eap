"""add tenant-scoped composite foreign keys (NOT VALID + VALIDATE)

Revision ID: 9c3a1f2b4d5e
Revises: e04d8bd8d64f
Create Date: 2026-09-11 16:00:00.000000

Enforce that tenant-scoped child rows can only reference users/agents
belonging to the same tenant (architecture §5.2).

Two-phase constraint rollout
----------------------------
The old schema permitted cross-tenant references, so a populated database may
already contain legacy violating rows. Creating the foreign keys with plain
validated DDL would fail on the first constraint and roll back the whole
migration with no actionable detail. Instead each key is created ``NOT VALID``
(enforced immediately for new writes, existing rows not yet scanned) and then
validated in a separate ``ALTER TABLE ... VALIDATE CONSTRAINT`` step. If legacy
cross-tenant rows exist, the validate step fails naming the exact table and
constraint; fix the data (see below) and re-run ``alembic upgrade head``.

Repairing legacy cross-tenant rows: for each named table, list orphan rows with
``SELECT * FROM <table> t LEFT JOIN users u ON u.id = t.<ref> AND u.tenant_id =
t.tenant_id WHERE t.<ref> IS NOT NULL AND u.id IS NULL`` (substitute the
``agent_registry`` parent for ``run_logs.agent_id``), then delete or re-map them
before re-running.

App-layer isolation boundary (TenantMiddleware)
-----------------------------------------------
The following tables have no ``tenant_id`` column, so a same-tenant invariant
cannot be expressed as a composite foreign key. Their tenant scope is derived
transitively from a parent entity and is enforced only by the application-layer
``TenantMiddleware``:

- ``refresh_tokens``            — tenant via ``users.id``
- ``agent_knowledge_bindings``  — tenant via ``agent_registry.agent_id``
- ``agent_tool_bindings``       — tenant via ``agent_registry.agent_id``
- ``agent_daily_stats``         — tenant via ``agent_registry.agent_id``
- ``tool_debug_cases``          — tenant via ``tool_registry.tool_id``
- ``trace_steps``               — tenant via ``run_logs.trace_id``
- ``trace_citations``           — tenant via ``run_logs.trace_id``
- ``trace_tool_calls``          — tenant via ``run_logs.trace_id``
- ``task_outbox_events``        — no FK by design (outbox rows must outlive tasks)
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c3a1f2b4d5e"
down_revision: str | None = "e04d8bd8d64f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (source table, local cols, referent table, remote cols, constraint name)
_COMPOSITE_FKS: tuple[tuple[str, list[str], str, list[str], str], ...] = (
    ("agent_registry", ["tenant_id", "created_by"], "users", ["tenant_id", "id"],
     "fk_agent_registry_tenant_created_by_users"),
    ("tool_registry", ["tenant_id", "created_by"], "users", ["tenant_id", "id"],
     "fk_tool_registry_tenant_created_by_users"),
    ("tasks", ["tenant_id", "creator_id"], "users", ["tenant_id", "id"],
     "fk_tasks_tenant_creator_id_users"),
    ("tasks", ["tenant_id", "assignee_id"], "users", ["tenant_id", "id"],
     "fk_tasks_tenant_assignee_id_users"),
    ("user_memories", ["tenant_id", "user_id"], "users", ["tenant_id", "id"],
     "fk_user_memories_tenant_user_id_users"),
    ("audit_logs", ["tenant_id", "user_id"], "users", ["tenant_id", "id"],
     "fk_audit_logs_tenant_user_id_users"),
    ("run_logs", ["tenant_id", "agent_id"], "agent_registry", ["tenant_id", "agent_id"],
     "fk_run_logs_tenant_agent_id_agent_registry"),
    ("run_logs", ["tenant_id", "user_id"], "users", ["tenant_id", "id"],
     "fk_run_logs_tenant_user_id_users"),
)


def upgrade() -> None:
    # Anchor unique constraints: composite FKs reference (tenant_id, id) /
    # (tenant_id, agent_id), which must be a unique key on the parent table.
    op.create_unique_constraint("uq_users_tenant_id_id", "users", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_agent_registry_tenant_agent_id", "agent_registry", ["tenant_id", "agent_id"]
    )

    # Phase 1: create FKs NOT VALID (enforced for new rows, existing rows not
    # scanned yet) so DDL never aborts on legacy cross-tenant data.
    for source, local_cols, referent, remote_cols, name in _COMPOSITE_FKS:
        op.create_foreign_key(
            name, source, referent, local_cols, remote_cols, postgresql_not_valid=True
        )

    # Phase 2: validate existing rows. Fails per-constraint if legacy
    # cross-tenant rows exist (see module docstring for repair guidance).
    # Identifiers are hardcoded (no string interpolation).
    op.execute(
        "ALTER TABLE agent_registry VALIDATE CONSTRAINT "
        "fk_agent_registry_tenant_created_by_users"
    )
    op.execute(
        "ALTER TABLE tool_registry VALIDATE CONSTRAINT "
        "fk_tool_registry_tenant_created_by_users"
    )
    op.execute(
        "ALTER TABLE tasks VALIDATE CONSTRAINT fk_tasks_tenant_creator_id_users"
    )
    op.execute(
        "ALTER TABLE tasks VALIDATE CONSTRAINT fk_tasks_tenant_assignee_id_users"
    )
    op.execute(
        "ALTER TABLE user_memories VALIDATE CONSTRAINT "
        "fk_user_memories_tenant_user_id_users"
    )
    op.execute(
        "ALTER TABLE audit_logs VALIDATE CONSTRAINT fk_audit_logs_tenant_user_id_users"
    )
    op.execute(
        "ALTER TABLE run_logs VALIDATE CONSTRAINT "
        "fk_run_logs_tenant_agent_id_agent_registry"
    )
    op.execute(
        "ALTER TABLE run_logs VALIDATE CONSTRAINT fk_run_logs_tenant_user_id_users"
    )


def downgrade() -> None:
    for source, _local, _referent, _remote, name in reversed(_COMPOSITE_FKS):
        op.drop_constraint(name, source, type_="foreignkey")
    op.drop_constraint("uq_agent_registry_tenant_agent_id", "agent_registry", type_="unique")
    op.drop_constraint("uq_users_tenant_id_id", "users", type_="unique")
