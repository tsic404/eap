"""Tenancy isolation helper tests (§8.3, ADR-004)."""

import uuid

from app.models.user import User
from app.tenancy import dify_user_identifier, tenant_scoped_select


def test_dify_user_identifier_encodes_tenant_and_user() -> None:
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    user_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    assert dify_user_identifier(tenant_id, user_id) == f"{tenant_id}:{user_id}"


def test_tenant_scoped_select_applies_tenant_filter() -> None:
    tenant_id = uuid.uuid4()
    statement = tenant_scoped_select(User, tenant_id)
    assert statement.whereclause is not None
    assert statement.whereclause.compare(User.tenant_id == tenant_id)


def test_tenant_scoped_select_compiles_to_tenant_predicate() -> None:
    tenant_id = uuid.uuid4()
    statement = tenant_scoped_select(User, tenant_id)
    sql = str(statement)
    assert "tenant_id" in sql
    assert "WHERE" in sql.upper()
