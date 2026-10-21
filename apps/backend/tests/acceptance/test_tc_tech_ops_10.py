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

    The bootstrap folds like ``app/auth/tenants.py`` (C-23/C-111): a
    ``str.lower()`` input against SQL ``lower()`` storage. Reusing that
    resolver for the admin email check keeps C-120 honest — an accepted admin
    is one the local login path actually reaches.
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
async def test_domains_local_login_cannot_resolve_are_refused(session_factory) -> None:  # type: ignore[no-untyped-def]
    """ß/İ domains fold differently in local login and storage, so they are refused.

    ``LocalLoginService`` casefolds the whole address before the resolver, while
    the resolver compares ``str.lower()`` with SQL ``lower()``. PostgreSQL keeps
    ß and maps İ to a plain i, so these domains can never be resolved by local
    login. Binding one — even with the capability off — would leave a tenant
    nobody can sign in for, so the run fails up front with its own judgement
    item and writes nothing (C-119 / C-121).
    """
    for domain in ("straße.de", "STRAẞE.DE", "İstanbul.com"):
        outcome, status = await _run(
            session_factory, domain=domain, email=f"admin@{domain}", local_login_enabled=False
        )
        assert outcome.result == BootstrapResult.FAILED, domain
        assert status != 0
        assert "DOMAIN_FOLD_INCONSISTENT" in outcome.reason, outcome.reason
    assert await _rows(session_factory, Tenant) == []
    assert await _rows(session_factory, User) == []


@pytest.mark.asyncio
async def test_refused_domain_is_unreachable_through_the_real_login_endpoint(
    session_factory,
) -> None:  # type: ignore[no-untyped-def]
    """The refused ß spelling also fails at ``POST /api/auth/login/local`` (C-111).

    Pre-existing data is what a refusal cannot prevent; the same address through
    the real endpoint casefolds to ``strasse.de`` and reaches no tenant, which
    is exactly why the bootstrap must not bind such a domain.
    """
    from harness import LOGIN_PATH, error_code, start

    await seed_tenant(session_factory, domain="straße.de", name="Strassen")
    harness = await start(session_factory)
    try:
        for email in ("admin@straße.de", "admin@STRAẞE.DE"):
            login = await harness.http.post(
                LOGIN_PATH, json={"email": email, "password": _PASSWORD}
            )
            assert login.status_code == 403, login.text
            assert error_code(login) == "TENANT_NOT_FOUND"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ascii_ss_domain_is_usable_and_login_reachable(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A domain whose folds agree (ASCII 'SS') is bound and its admin can sign in."""
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
