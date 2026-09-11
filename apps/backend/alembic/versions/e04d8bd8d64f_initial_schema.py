"""initial schema

Revision ID: e04d8bd8d64f
Revises:
Create Date: 2026-09-11 15:00:23.218009

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e04d8bd8d64f"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL extensions the schema depends on: pgvector for
    # UserMemory.embedding, pgcrypto for gen_random_uuid().
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "task_outbox_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_outbox_events")),
    )
    op.create_index(
        "ix_task_outbox_delivered_created",
        "task_outbox_events",
        ["delivered_at", "created_at"],
        unique=False,
    )
    op.create_table(
        "tenants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("sso_domain", sa.String(length=255), nullable=True),
        sa.Column("sso_provider", sa.String(length=50), nullable=False),
        sa.Column("sso_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("quota_limit", sa.Integer(), nullable=False),
        sa.Column("quota_used", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
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
            "quota_limit >= 0 AND quota_used >= 0", name="ck_tenants_quota_non_negative"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
    )
    op.create_table(
        "knowledge_base_registry",
        sa.Column("kb_id", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("dify_dataset_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=True),
        sa.Column("authorized_scope", sa.String(length=255), nullable=True),
        sa.Column("indexing_status", sa.String(length=50), nullable=True),
        sa.Column("doc_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
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
            "indexing_status IN ('ready', 'indexing', 'failed')",
            name="ck_knowledge_base_registry_indexing_status",
        ),
        sa.CheckConstraint(
            "doc_count >= 0 AND chunk_count >= 0",
            name="ck_knowledge_base_registry_counts_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_knowledge_base_registry_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("kb_id", name=op.f("pk_knowledge_base_registry")),
    )
    op.create_index(
        "ix_knowledge_base_registry_tenant_id",
        "knowledge_base_registry",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("sso_sub", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("department", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("avatar_text", sa.String(length=10), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
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
            "role IN ('platform_admin', 'agent_admin', 'knowledge_admin', 'auditor', 'employee')",
            name="ck_users_role",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_users_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        sa.UniqueConstraint("tenant_id", "sso_sub", name="uq_users_tenant_sso_sub"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"], unique=False)
    op.create_table(
        "agent_registry",
        sa.Column("agent_id", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("dify_app_id", sa.String(length=100), nullable=False),
        sa.Column("dify_api_key", sa.Text(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("visibility", sa.String(length=50), nullable=False),
        sa.Column("icon", sa.String(length=50), nullable=True),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("model_provider", sa.String(length=255), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.UUID(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('draft', 'testing', 'published', 'offline')",
            name="ck_agent_registry_status",
        ),
        sa.CheckConstraint(
            "type IN ('chat', 'workflow', 'agent', 'data')", name="ck_agent_registry_type"
        ),
        sa.CheckConstraint("version >= 1", name="ck_agent_registry_version"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_agent_registry_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_agent_registry_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("agent_id", name=op.f("pk_agent_registry")),
    )
    op.create_index("ix_agent_registry_tenant_id", "agent_registry", ["tenant_id"], unique=False)
    op.create_index(
        "ix_agent_registry_tenant_status", "agent_registry", ["tenant_id", "status"], unique=False
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_audit_logs_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_audit_logs_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_tenant_created_at", "audit_logs", ["tenant_id", "created_at"], unique=False
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("creator_id", sa.UUID(), nullable=False),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'executing', 'completed', "
            "'failed', 'cancelled')",
            name="ck_tasks_status",
        ),
        sa.CheckConstraint(
            "retry_count >= 0 AND max_retries >= 0", name="ck_tasks_retry_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["users.id"], name=op.f("fk_tasks_assignee_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["creator_id"], ["users.id"], name=op.f("fk_tasks_creator_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tasks_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_index(
        "ix_tasks_tenant_assignee_status",
        "tasks",
        ["tenant_id", "assignee_id", "status"],
        unique=False,
    )
    op.create_table(
        "tool_registry",
        sa.Column("tool_id", sa.String(length=100), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("endpoint", sa.String(length=2048), nullable=True),
        sa.Column("method", sa.String(length=10), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("permission_mode", sa.String(length=20), nullable=False),
        sa.Column("auth_type", sa.String(length=50), nullable=True),
        sa.Column("auth_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("timeout_ms", sa.Integer(), nullable=False),
        sa.Column("retry_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("circuit_breaker", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
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
            "permission_mode IN ('auto', 'confirm', 'disabled')",
            name="ck_tool_registry_permission_mode",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')", name="ck_tool_registry_risk_level"
        ),
        sa.CheckConstraint("timeout_ms >= 0", name="ck_tool_registry_timeout_ms"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_tool_registry_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tool_registry_tenant_id_tenants")
        ),
        sa.PrimaryKeyConstraint("tool_id", name=op.f("pk_tool_registry")),
    )
    op.create_index("ix_tool_registry_tenant_id", "tool_registry", ["tenant_id"], unique=False)
    op.create_table(
        "user_memories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(dim=1536), nullable=True),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("access_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
            "importance >= 0 AND importance <= 1 AND access_count >= 0",
            name="ck_user_memories_ranges",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_user_memories_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_memories_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_memories")),
    )
    op.create_index(
        "ix_user_memories_user_tenant", "user_memories", ["user_id", "tenant_id"], unique=False
    )
    op.create_table(
        "agent_daily_stats",
        sa.Column("agent_id", sa.String(length=100), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("successes", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("total_latency_ms", sa.BigInteger(), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "calls >= 0 AND successes >= 0 AND failures >= 0 AND total_latency_ms >= 0 "
            "AND total_tokens >= 0",
            name="ck_agent_daily_stats_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_registry.agent_id"],
            name=op.f("fk_agent_daily_stats_agent_id_agent_registry"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("agent_id", "stat_date", name=op.f("pk_agent_daily_stats")),
    )
    op.create_table(
        "agent_knowledge_bindings",
        sa.Column("agent_id", sa.String(length=100), nullable=False),
        sa.Column("kb_id", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_registry.agent_id"],
            name=op.f("fk_agent_knowledge_bindings_agent_id_agent_registry"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_base_registry.kb_id"],
            name=op.f("fk_agent_knowledge_bindings_kb_id_knowledge_base_registry"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("agent_id", "kb_id", name=op.f("pk_agent_knowledge_bindings")),
    )
    op.create_table(
        "agent_tool_bindings",
        sa.Column("agent_id", sa.String(length=100), nullable=False),
        sa.Column("tool_id", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_registry.agent_id"],
            name=op.f("fk_agent_tool_bindings_agent_id_agent_registry"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id"],
            ["tool_registry.tool_id"],
            name=op.f("fk_agent_tool_bindings_tool_id_tool_registry"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("agent_id", "tool_id", name=op.f("pk_agent_tool_bindings")),
    )
    op.create_table(
        "run_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("agent_name", sa.String(length=255), nullable=True),
        sa.Column("user_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("input", sa.Text(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("token_usage", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("knowledge_hit_count", sa.Integer(), nullable=False),
        sa.Column("dify_message_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('success', 'failed', 'running', 'blocked')", name="ck_run_logs_status"
        ),
        sa.CheckConstraint(
            "token_usage >= 0 AND latency_ms >= 0 AND tool_call_count >= 0 "
            "AND knowledge_hit_count >= 0",
            name="ck_run_logs_metrics_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_registry.agent_id"],
            name=op.f("fk_run_logs_agent_id_agent_registry"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_run_logs_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_run_logs_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_logs")),
        sa.UniqueConstraint("trace_id", name=op.f("uq_run_logs_trace_id")),
    )
    op.create_index(
        "ix_run_logs_tenant_created_at", "run_logs", ["tenant_id", "created_at"], unique=False
    )
    op.create_table(
        "tool_debug_cases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tool_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("request_body", sa.Text(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "latency_ms >= 0 AND (status_code IS NULL OR "
            "(status_code >= 100 AND status_code <= 599))",
            name="ck_tool_debug_cases_ranges",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id"],
            ["tool_registry.tool_id"],
            name=op.f("fk_tool_debug_cases_tool_id_tool_registry"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_debug_cases")),
    )
    op.create_table(
        "trace_citations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("kb_name", sa.String(length=255), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["run_logs.trace_id"], name=op.f("fk_trace_citations_trace_id_run_logs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trace_citations")),
    )
    op.create_table(
        "trace_steps",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.CheckConstraint("step_order >= 0", name="ck_trace_steps_step_order"),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["run_logs.trace_id"], name=op.f("fk_trace_steps_trace_id_run_logs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trace_steps")),
    )
    op.create_table(
        "trace_tool_calls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("tool_name", sa.String(length=255), nullable=True),
        sa.Column("tool_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("permission_mode", sa.String(length=20), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("request_summary", sa.Text(), nullable=True),
        sa.Column("response_summary", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["run_logs.trace_id"], name=op.f("fk_trace_tool_calls_trace_id_run_logs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trace_tool_calls")),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("trace_tool_calls")
    op.drop_table("trace_steps")
    op.drop_table("trace_citations")
    op.drop_table("tool_debug_cases")
    op.drop_index("ix_run_logs_tenant_created_at", table_name="run_logs")
    op.drop_table("run_logs")
    op.drop_table("agent_tool_bindings")
    op.drop_table("agent_knowledge_bindings")
    op.drop_table("agent_daily_stats")
    op.drop_index("ix_user_memories_user_tenant", table_name="user_memories")
    op.drop_table("user_memories")
    op.drop_index("ix_tool_registry_tenant_id", table_name="tool_registry")
    op.drop_table("tool_registry")
    op.drop_index("ix_tasks_tenant_assignee_status", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_audit_logs_tenant_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_agent_registry_tenant_status", table_name="agent_registry")
    op.drop_index("ix_agent_registry_tenant_id", table_name="agent_registry")
    op.drop_table("agent_registry")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_table("users")
    op.drop_index("ix_knowledge_base_registry_tenant_id", table_name="knowledge_base_registry")
    op.drop_table("knowledge_base_registry")
    op.drop_table("tenants")
    op.drop_index("ix_task_outbox_delivered_created", table_name="task_outbox_events")
    op.drop_table("task_outbox_events")
    # ### end Alembic commands ###
