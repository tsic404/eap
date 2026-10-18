"""Design acceptance case TC-tech-auth-02.

The role gate and its fixed evaluation order, plus the member-management
invariants (last platform admin, self-deactivation, role vocabulary).

Only the platform's own HTTP surface is observed: status codes, the stable
error code in the body, and follow-up reads through the same API.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
import redis.asyncio as aioredis

from app.auth.revocation import revoked_jti_key
from app.models.tenant import Tenant
from app.models.user import User

# The imports above must precede every other statement; the case identifier
# leads the module body from here on.
TC_ID = "TC-tech-auth-02"

_AGENTS = "/api/agents"
_MEMBERS = "/api/members"
_CREATE_AGENT_BODY = {"agentId": "demo-agent", "name": "Demo Agent"}
# C-29 fixes the *semantics* of deactivation, not the literal status value, and
# the member-management API that would carry it is absent at main@68ad4b5. The
# module therefore uses the one vocabulary its own gate case seeds.
_DEACTIVATED = "suspended"
# C-28: the member-management API is absent at main@68ad4b5, and the design does
# not fix the request shape (endpoint, method, body field names).
_MEMBER_API_XFAIL = pytest.mark.xfail(
    reason=(
        "C-28 member API not implemented at main@68ad4b5; "
        "the list/detail request shape is undecided by the design"
    ),
    strict=False,
)
_MEMBER_INVARIANT_XFAIL = pytest.mark.xfail(
    reason=(
        "C-28 member API not implemented at main@68ad4b5, so the C-29 invariant "
        "cannot be exercised; the PATCH request shape is undecided by the design"
    ),
    strict=False,
)


# ── seeding and signing helpers ─────────────────────────────────────────────


def _new_user(tenant: Tenant, *, role: str = "employee", status: str = "active") -> User:
    return User(
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        name="Test User",
        role=role,
        status=status,
    )


async def _seed_tenant_user(
    api: Any,
    *,
    role: str = "employee",
    user_status: str = "active",
    tenant_status: str = "active",
    quota_used: int = 0,
    quota_limit: int = 10000,
) -> tuple[Tenant, User]:
    """Seed one tenant with a single member in the requested gate state."""
    async with api.db.session() as session:
        tenant = Tenant(
            name="Acme",
            slug=f"acme-{uuid.uuid4().hex[:8]}",
            sso_provider="local",
            status=tenant_status,
            quota_used=quota_used,
            quota_limit=quota_limit,
        )
        session.add(tenant)
        await session.flush()
        user = _new_user(tenant, role=role, status=user_status)
        session.add(user)
        await session.commit()
        return tenant, user


async def _seed_member(
    api: Any, tenant: Tenant, *, role: str = "employee", status: str = "active"
) -> User:
    """Seed one more member of an existing tenant."""
    async with api.db.session() as session:
        user = _new_user(tenant, role=role, status=status)
        session.add(user)
        await session.commit()
        return user


def _sign(
    api: Any,
    user: User,
    tenant: Tenant | None,
    *,
    role: str | None = None,
    jti: str | None = None,
    tenant_id: uuid.UUID | None = None,
) -> str:
    """Sign an access token; ``tenant``/``tenant_id`` shaped for the gate under test."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "role": role or user.role,
        "jti": jti or uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(seconds=900),
    }
    resolved_tenant_id = tenant_id if tenant_id is not None else (tenant.id if tenant else None)
    if resolved_tenant_id is not None:
        payload["tenantId"] = str(resolved_tenant_id)
    return jwt.encode(payload, api.private_key, algorithm="RS256")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _revocation_registry(api: Any) -> AsyncIterator[None]:
    """Attach the async Redis client the access-token revocation gate reads."""
    client = aioredis.from_url(api.settings.redis_url)
    api.app.state.redis = client
    try:
        yield
    finally:
        await client.aclose()


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


# ── C-13: fixed gate order ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_without_identity_is_unauthorized(api) -> None:
    response = await api.client.get(_AGENTS)
    assert response.status_code == 401, response.text
    assert _code(response) == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_call_with_a_revoked_access_token_is_token_revoked(api) -> None:
    async with _revocation_registry(api):
        tenant, user = await _seed_tenant_user(api, role="agent_admin")
        jti = uuid.uuid4().hex
        headers = _bearer(_sign(api, user, tenant, jti=jti))
        # Register the access token's identifier, as logout does on its way out.
        api.redis_conn.set(revoked_jti_key(jti), "1", ex=900)

        response = await api.client.get(_AGENTS, headers=headers)
        assert response.status_code == 401, response.text
        assert _code(response) == "TOKEN_REVOKED"


@pytest.mark.asyncio
async def test_call_from_a_non_active_user_is_unauthorized(api) -> None:
    tenant, user = await _seed_tenant_user(api, role="agent_admin", user_status=_DEACTIVATED)
    response = await api.client.get(_AGENTS, headers=_bearer(_sign(api, user, tenant)))
    assert response.status_code == 401, response.text
    assert _code(response) == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_call_without_a_tenant_context_is_unauthorized(api) -> None:
    tenant, user = await _seed_tenant_user(api, role="agent_admin")
    response = await api.client.get(_AGENTS, headers=_bearer(_sign(api, user, None)))
    assert response.status_code == 401, response.text
    assert _code(response) == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_cross_tenant_request_from_a_non_admin_is_forbidden_before_any_lookup(api) -> None:
    tenant, user = await _seed_tenant_user(api, role="agent_admin")
    headers = _bearer(_sign(api, user, None))
    # A tenant id that does not exist must still be refused as cross-tenant:
    # were the gate to consult the database first, this would be a 404 and a
    # caller could probe for tenant existence.
    headers["X-Tenant-Id"] = str(uuid.uuid4())

    response = await api.client.get(_AGENTS, headers=headers)
    assert response.status_code == 403, response.text
    assert _code(response) == "FORBIDDEN"


@pytest.mark.asyncio
async def test_unknown_tenant_for_a_platform_admin_is_not_found(api) -> None:
    tenant, user = await _seed_tenant_user(api, role="platform_admin")
    headers = _bearer(_sign(api, user, tenant, tenant_id=uuid.uuid4()))
    # A platform admin may act cross-tenant, so the unknown tenant is looked up
    # and reported missing rather than refused as a cross-tenant request.
    response = await api.client.get(_AGENTS, headers=headers)
    assert response.status_code == 404, response.text
    assert _code(response) == "NOT_FOUND"


@pytest.mark.asyncio
async def test_suspended_tenant_is_rejected(api) -> None:
    tenant, user = await _seed_tenant_user(api, role="agent_admin", tenant_status="suspended")
    response = await api.client.get(_AGENTS, headers=_bearer(_sign(api, user, tenant)))
    assert response.status_code == 403, response.text
    assert _code(response) == "TENANT_SUSPENDED"


@pytest.mark.asyncio
async def test_exhausted_tenant_quota_is_rejected(api) -> None:
    tenant, user = await _seed_tenant_user(api, role="agent_admin", quota_used=10, quota_limit=10)
    response = await api.client.get(_AGENTS, headers=_bearer(_sign(api, user, tenant)))
    assert response.status_code == 429, response.text
    assert _code(response) == "QUOTA_EXCEEDED"


@pytest.mark.asyncio
async def test_employee_write_on_an_admin_endpoint_is_forbidden(api) -> None:
    tenant, user = await _seed_tenant_user(api, role="employee")
    response = await api.client.post(
        _AGENTS, headers=_bearer(_sign(api, user, tenant)), json=_CREATE_AGENT_BODY
    )
    assert response.status_code == 403, response.text
    assert _code(response) == "FORBIDDEN"


@pytest.mark.asyncio
async def test_auditor_write_on_an_admin_endpoint_is_forbidden(api) -> None:
    tenant, user = await _seed_tenant_user(api, role="auditor")
    response = await api.client.post(
        _AGENTS, headers=_bearer(_sign(api, user, tenant)), json=_CREATE_AGENT_BODY
    )
    assert response.status_code == 403, response.text
    assert _code(response) == "FORBIDDEN"


@pytest.mark.asyncio
async def test_tenant_gate_is_evaluated_before_the_role_gate(api) -> None:
    tenant, user = await _seed_tenant_user(
        api, role="employee", tenant_status="suspended", quota_used=10, quota_limit=10
    )
    response = await api.client.post(
        _AGENTS, headers=_bearer(_sign(api, user, tenant)), json=_CREATE_AGENT_BODY
    )
    # Tenant status and quota are checked before the endpoint's role set, so a
    # suspended tenant must report TENANT_SUSPENDED rather than FORBIDDEN.
    assert response.status_code == 403, response.text
    assert _code(response) == "TENANT_SUSPENDED"


# ── C-12 / C-28 / C-29: member management ──────────────────────────────────


@pytest.mark.asyncio
@_MEMBER_API_XFAIL
async def test_auditor_member_management_is_forbidden(api) -> None:
    tenant, auditor = await _seed_tenant_user(api, role="auditor")
    response = await api.client.get(_MEMBERS, headers=_bearer(_sign(api, auditor, tenant)))
    assert response.status_code == 403, response.text
    assert _code(response) == "FORBIDDEN"


@pytest.mark.asyncio
@_MEMBER_INVARIANT_XFAIL
async def test_member_role_outside_the_role_ladder_is_a_validation_error(api) -> None:
    tenant, admin = await _seed_tenant_user(api, role="platform_admin")
    employee = await _seed_member(api, tenant, role="employee")
    response = await api.client.patch(
        f"{_MEMBERS}/{employee.id}",
        headers=_bearer(_sign(api, admin, tenant)),
        json={"role": "superuser"},
    )
    assert response.status_code == 422, response.text
    assert _code(response) == "VALIDATION_ERROR"


@pytest.mark.asyncio
@_MEMBER_INVARIANT_XFAIL
async def test_demoting_the_last_platform_admin_is_refused(api) -> None:
    tenant, admin = await _seed_tenant_user(api, role="platform_admin")
    response = await api.client.patch(
        f"{_MEMBERS}/{admin.id}",
        headers=_bearer(_sign(api, admin, tenant)),
        json={"role": "employee"},
    )
    assert response.status_code == 409, response.text
    assert _code(response) == "LAST_ADMIN"


@pytest.mark.asyncio
@_MEMBER_INVARIANT_XFAIL
async def test_disabling_the_last_platform_admin_is_refused(api) -> None:
    tenant, admin = await _seed_tenant_user(api, role="platform_admin")
    response = await api.client.patch(
        f"{_MEMBERS}/{admin.id}",
        headers=_bearer(_sign(api, admin, tenant)),
        json={"status": _DEACTIVATED},
    )
    assert response.status_code == 409, response.text
    assert _code(response) == "LAST_ADMIN"


@pytest.mark.asyncio
@_MEMBER_INVARIANT_XFAIL
async def test_disabling_oneself_is_refused(api) -> None:
    tenant, admin = await _seed_tenant_user(api, role="platform_admin")
    # A second platform admin keeps the tenant's last-admin invariant out of
    # the way, so the refusal can only come from the self-deactivation rule.
    await _seed_member(api, tenant, role="platform_admin")
    response = await api.client.patch(
        f"{_MEMBERS}/{admin.id}",
        headers=_bearer(_sign(api, admin, tenant)),
        json={"status": _DEACTIVATED},
    )
    assert response.status_code == 409, response.text
    assert _code(response) == "SELF_DISABLE"


@pytest.mark.asyncio
@_MEMBER_API_XFAIL
async def test_platform_admin_changes_a_member_role(api) -> None:
    tenant, admin = await _seed_tenant_user(api, role="platform_admin")
    employee = await _seed_member(api, tenant, role="employee")
    headers = _bearer(_sign(api, admin, tenant))

    changed = await api.client.patch(
        f"{_MEMBERS}/{employee.id}", headers=headers, json={"role": "auditor"}
    )
    assert changed.status_code == 200, changed.text

    read = await api.client.get(f"{_MEMBERS}/{employee.id}", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["data"]["role"] == "auditor"
