"""TC-tech-ops-10 — bootstrap idempotence, same-domain re-entry, skip, concurrency.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-ops-10 → AC-T19) over
``docs/02-contracts.md`` C-119 / C-120 / C-121. The bootstrap action is driven
as a module against PostgreSQL; judgements cover the folded-domain match rule
and the skip result of C-120 ① (any platform admin, either source, active or
not, stops account creation without rewriting anything).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from harness import seed_tenant, seed_user
from sqlalchemy import select
from sqlalchemy.orm import noload

from app.auth.password import hash_password, verify_password
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.user import ACCOUNT_SOURCE_SSO, User
from app.production_bootstrap import (
    ACTION_ADMIN_CREATED,
    ACTION_TENANT_CREATED,
    BootstrapResult,
    bootstrap,
    exit_status,
)

TC_ID = "TC-tech-ops-10"

_NAME = "Acme"
_DOMAIN = "acme.com"
_EMAIL = "admin@acme.com"
_PASSWORD = "initial-bootstrap-pw-10"
_NEW_PASSWORD = "post-change-pw-11"


async def _run(
    session_factory: Any,
    *,
    name: str = _NAME,
    domain: str = _DOMAIN,
    email: str = _EMAIL,
    password: str = _PASSWORD,
    local_login_enabled: bool = True,
) -> tuple[Any, int]:
    async with session_factory() as session:
        outcome = await bootstrap(
            session,
            tenant_name=name,
            tenant_domain=domain,
            admin_email=email,
            admin_name="Platform Admin",
            admin_password=password,
            local_login_enabled=local_login_enabled,
        )
        return outcome, exit_status(outcome.result)


async def _rows(session_factory: Any, model: Any) -> list[Any]:
    async with session_factory() as session:
        rows = await session.execute(select(model).options(noload("*")))
        return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_repeat_runs_are_idempotent_and_do_not_reset_credentials(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A second run adds nothing and never resets the password or the change flag."""
    first, status = await _run(session_factory)
    assert first.result == BootstrapResult.SUCCESS
    assert status == 0

    # The operator changed the initial password on his first login; an
    # idempotent bootstrap must leave that change standing (C-120).
    async with session_factory() as session:
        admin = (await session.execute(select(User))).scalars().first()
        assert admin is not None
        admin.must_change_password = False
        admin.password_hash = hash_password(_NEW_PASSWORD)
        await session.commit()

    again, status_again = await _run(session_factory)
    assert again.result == BootstrapResult.SKIPPED
    assert status_again == 0

    tenants = await _rows(session_factory, Tenant)
    assert len(tenants) == 1
    users = await _rows(session_factory, User)
    assert len(users) == 1
    admin = users[0]
    assert admin.must_change_password is False
    assert admin.password_hash is not None
    assert verify_password(_NEW_PASSWORD, admin.password_hash)
    assert not verify_password(_PASSWORD, admin.password_hash)
    audits = await _rows(session_factory, AuditLog)
    assert len(audits) == 2
    assert {audit.action for audit in audits} == {ACTION_TENANT_CREATED, ACTION_ADMIN_CREATED}


@pytest.mark.asyncio
async def test_same_domain_different_case_or_name_finds_existing_tenant(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Another casing, or another tenant name, still hits the one existing tenant."""
    _, status = await _run(session_factory)
    assert status == 0

    outcome, status = await _run(
        session_factory, name="Completely Different", domain="ACME.COM", password=_PASSWORD
    )
    assert outcome.result == BootstrapResult.SKIPPED
    assert status == 0

    tenants = await _rows(session_factory, Tenant)
    assert len(tenants) == 1
    assert tenants[0].name == "Acme"
    assert tenants[0].sso_domain == "acme.com"
    assert tenants[0].status == "active"

    users = await _rows(session_factory, User)
    assert len(users) == 1
    assert users[0].email == _EMAIL


@pytest.mark.asyncio
async def test_non_ascii_case_variant_hits_existing_tenant_and_admin_logs_in(
    session_factory,
) -> None:  # type: ignore[no-untyped-def]
    """Ü/ü folds identically on both sides: re-entry hits, and the admin signs in.

    The bootstrap folds the domain with the login paths' own operator (C-122),
    and reuses the resolver for the admin email check so an accepted admin is
    one the local login path actually reaches (C-120).
    """
    from harness import LOGIN_PATH, start

    tenant = await seed_tenant(session_factory, domain="BÜRO.DE", name="Büro")
    outcome, status = await _run(session_factory, domain="büro.de", email="admin@büro.de")
    assert outcome.result == BootstrapResult.SUCCESS
    assert status == 0

    tenants = await _rows(session_factory, Tenant)
    assert len(tenants) == 1
    assert tenants[0].id == tenant.id
    assert tenants[0].name == "Büro"  # untouched by the re-entry (C-119)
    users = await _rows(session_factory, User)
    assert len(users) == 1
    assert users[0].email == "admin@büro.de"

    harness = await start(session_factory)
    try:
        login = await harness.http.post(
            LOGIN_PATH, json={"email": "admin@büro.de", "password": _PASSWORD}
        )
        assert login.status_code == 200, login.text
        assert login.json() == {"data": {"mustChangePassword": True}}
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ss_domain_reentry_through_its_capital_variant_is_idempotent(
    session_factory,
) -> None:  # type: ignore[no-untyped-def]
    """``STRAẞE.DE`` names the tenant bound as ``straße.de``; nothing is rewritten.

    C-122 folds ``ẞ`` to ``ß``, so the second run hits the existing tenant and
    C-120 ① skips account creation — the tenant's name and stored domain keep
    the text the first run gave them (C-119).
    """
    _, status = await _run(session_factory, domain="straße.de", email="admin@straße.de")
    assert status == 0

    outcome, status = await _run(
        session_factory,
        name="Completely Different",
        domain="STRAẞE.DE",
        email="admin@straße.de",
    )
    assert outcome.result == BootstrapResult.SKIPPED
    assert status == 0

    tenants = await _rows(session_factory, Tenant)
    assert len(tenants) == 1
    assert tenants[0].name == _NAME
    assert tenants[0].sso_domain == "straße.de"
    assert tenants[0].status == "active"
    users = await _rows(session_factory, User)
    assert len(users) == 1


@pytest.mark.asyncio
async def test_dotted_capital_i_domain_binds_and_resolves(session_factory) -> None:  # type: ignore[no-untyped-def]
    """``İstanbul.com`` binds, and its admin signs in under another casing (C-122).

    ``İ`` folds to ``i`` plus a combining dot, so ``İSTANBUL.COM`` and
    ``İstanbul.com`` are one domain — and the stored text keeps the dot pattern
    the deployment supplied.
    """
    from harness import LOGIN_PATH, start

    outcome, status = await _run(session_factory, domain="İstanbul.com", email="admin@İstanbul.com")
    assert outcome.result == BootstrapResult.SUCCESS
    assert status == 0
    tenants = await _rows(session_factory, Tenant)
    assert [t.sso_domain for t in tenants] == ["İstanbul.com"]

    harness = await start(session_factory)
    try:
        login = await harness.http.post(
            LOGIN_PATH, json={"email": "admin@İSTANBUL.COM", "password": _PASSWORD}
        )
        assert login.status_code == 200, login.text
        assert login.json() == {"data": {"mustChangePassword": True}}
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ascii_ss_domain_is_usable_and_login_reachable(session_factory) -> None:  # type: ignore[no-untyped-def]
    """An ASCII ``SS`` domain is bound as given and its admin can sign in (C-122)."""
    from harness import LOGIN_PATH, start

    outcome, status = await _run(session_factory, domain="STRASSE.DE", email="admin@STRASSE.DE")
    assert outcome.result == BootstrapResult.SUCCESS
    assert status == 0
    tenants = await _rows(session_factory, Tenant)
    assert [t.sso_domain for t in tenants] == ["STRASSE.DE"]

    harness = await start(session_factory)
    try:
        login = await harness.http.post(
            LOGIN_PATH, json={"email": "admin@STRASSE.DE", "password": _PASSWORD}
        )
        assert login.status_code == 200, login.text
        assert login.json() == {"data": {"mustChangePassword": True}}
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_an_admin_of_any_source_skips_creation(session_factory) -> None:  # type: ignore[no-untyped-def]
    """C-120 ① counts any platform admin, SSO or local, active or not."""
    tenant = await seed_tenant(session_factory, domain=_DOMAIN, name=_NAME)
    await seed_user(
        session_factory,
        tenant,
        email="boss@acme.com",
        role="platform_admin",
        status="inactive",
    )

    outcome, status = await _run(session_factory)
    assert outcome.result == BootstrapResult.SKIPPED
    assert status == 0

    users = await _rows(session_factory, User)
    assert len(users) == 1
    boss = users[0]
    assert boss.email == "boss@acme.com"
    assert boss.role == "platform_admin"
    assert boss.status == "inactive"
    assert boss.account_source == ACCOUNT_SOURCE_SSO
    assert boss.must_change_password is False
    # Nothing was touched: no new tenant, no audit records, boss unchanged.
    tenants = await _rows(session_factory, Tenant)
    assert len(tenants) == 1
    assert tenants[0].id == tenant.id
    assert await _rows(session_factory, AuditLog) == []


@pytest.mark.asyncio
async def test_concurrent_runs_produce_one_tenant_and_one_admin(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Parallel runs of the same input leave one folded domain and one first admin."""
    outcomes = await asyncio.gather(*[_run(session_factory) for _ in range(4)])
    results = [outcome.result for outcome, _ in outcomes]
    assert results.count(BootstrapResult.SUCCESS) == 1
    assert all(r in (BootstrapResult.SUCCESS, BootstrapResult.SKIPPED) for r in results)

    tenants = await _rows(session_factory, Tenant)
    assert len(tenants) == 1
    users = await _rows(session_factory, User)
    assert len([u for u in users if u.role == "platform_admin"]) == 1
