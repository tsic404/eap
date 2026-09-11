"""Model-layer contract tests (no live database required)."""

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import configure_mappers

import app.models  # noqa: F401  # registers every model on Base.metadata
from app.models.base import Base

EXPECTED_TABLES = {
    "agent_daily_stats",
    "agent_knowledge_bindings",
    "agent_registry",
    "agent_tool_bindings",
    "audit_logs",
    "knowledge_base_registry",
    "refresh_tokens",
    "run_logs",
    "task_outbox_events",
    "tasks",
    "tenants",
    "tool_debug_cases",
    "tool_registry",
    "trace_citations",
    "trace_steps",
    "trace_tool_calls",
    "user_memories",
    "users",
}


def test_all_models_registered_with_snake_case_table_names() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_mappers_configure_without_relationship_errors() -> None:
    # Raises on ambiguous/broken relationship targets (e.g. missing back_populates).
    configure_mappers()


def test_user_memory_embedding_is_pgvector_1536() -> None:
    embedding = Base.metadata.tables["user_memories"].columns["embedding"]
    assert isinstance(embedding.type, Vector)
    assert embedding.type.dim == 1536  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("users", {"uq_users_tenant_sso_sub", "uq_users_tenant_email"}),
        ("tenants", {"uq_tenants_slug"}),
        ("refresh_tokens", {"uq_refresh_tokens_token_hash"}),
        ("run_logs", {"uq_run_logs_trace_id"}),
    ],
)
def test_unique_constraints(table: str, expected: set[str]) -> None:
    names = {c.name for c in Base.metadata.tables[table].constraints if c.name}
    assert expected <= names


def test_tenant_scoped_indexes_present() -> None:
    tables = Base.metadata.tables
    assert "ix_users_tenant_id" in {i.name for i in tables["users"].indexes}
    assert "ix_agent_registry_tenant_status" in {i.name for i in tables["agent_registry"].indexes}
    assert "ix_run_logs_tenant_created_at" in {i.name for i in tables["run_logs"].indexes}


def test_foreign_keys_wired() -> None:
    tables = Base.metadata.tables
    assert "tenants.id" in {fk.target_fullname for fk in tables["users"].foreign_keys}
    assert "tenants.id" in {fk.target_fullname for fk in tables["agent_registry"].foreign_keys}
    assert "users.id" in {fk.target_fullname for fk in tables["tasks"].foreign_keys}


def test_check_constraints_present() -> None:
    from sqlalchemy import CheckConstraint

    tables = Base.metadata.tables
    assert "ck_users_role" in {
        c.name for c in tables["users"].constraints if isinstance(c, CheckConstraint)
    }
    assert "ck_tasks_status" in {
        c.name for c in tables["tasks"].constraints if isinstance(c, CheckConstraint)
    }
    assert "ck_user_memories_ranges" in {
        c.name for c in tables["user_memories"].constraints if isinstance(c, CheckConstraint)
    }


def test_collection_relationships_use_selectin() -> None:
    from app.models.tenant import Tenant
    from app.models.user import User

    assert Tenant.users.property.lazy == "selectin"
    assert User.run_logs.property.lazy == "selectin"
