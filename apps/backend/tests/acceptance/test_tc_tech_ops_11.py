"""TC-tech-ops-11 — ambiguity, rejections, closed capability, no partial writes.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-ops-11 → AC-T19, F7
negative) over ``docs/02-contracts.md`` C-119 / C-120 / C-121 / C-110 / C-114.
The bootstrap action is driven as a module against PostgreSQL; each failed
judgement is observed to leave the database exactly as it was, and the four
results stay distinguishable by name and exit status.
"""

from __future__ import annotations

from typing import Any

import pytest
from harness import error_code, seed_tenant, seed_user
from sqlalchemy import select
from sqlalchemy.orm import noload

from app.auth.password import verify_password
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.user import ACCOUNT_SOURCE_LOCAL, User
from app.production_bootstrap import (
    ACTION_TENANT_CREATED,
    BootstrapResult,
    bootstrap,
    exit_status,
)

TC_ID = "TC-tech-ops-11"

_NAME = "Acme"
_DOMAIN = "acme.com"
_EMAIL = "admin@acme.com"
_PASSWORD = "initial-bootstrap-pw-11"
_SHORT_PASSWORD = "seven77"
_OTHER_PASSWORD = "initial-bootstrap-pw-12"


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


async def _snapshot(session_factory: Any) -> tuple[int, int, int]:
    """(tenants, users, audit rows) counts of the whole database."""
    return (
        len(await _rows(session_factory, Tenant)),
        len(await _rows(session_factory, User)),
        len(await _rows(session_factory, AuditLog)),
    )


@pytest.mark.asyncio
async def test_two_fold_equal_domains_fail_ambiguously_without_writes(session_factory) -> None:  # type: ignore[no-untyped-def]
    """TENANT_AMBIGUOUS is distinguishable and leaves no write of this run."""
    await seed_tenant(session_factory, domain="acme.com", name="Acme One")
    await seed_tenant(session_factory, domain="ACME.COM", name="Acme Two")

    outcome, status = await _run(session_factory, name="Third", domain="Acme.Com")
    assert outcome.result == BootstrapResult.FAILED
    assert status != 0
    assert "AMBIGUOUS" in outcome.reason

    tenants = await _rows(session_factory, Tenant)
    assert len(tenants) == 2
    assert sorted(t.sso_domain for t in tenants) == ["ACME.COM", "acme.com"]
    assert await _rows(session_factory, User) == []
    assert await _rows(session_factory, AuditLog) == []


@pytest.mark.asyncio
async def test_fold_inequivalent_domains_coexist_and_each_resolves_its_own(
    session_factory,
) -> None:  # type: ignore[no-untyped-def]
    """``straße.de`` and ``strasse.de`` are two domains: no ambiguity, own logins.

    C-122 keeps ``ß`` and ``ss`` apart, so binding one never counts the other as
    a second match, and each login spelling reaches only the tenant it folds to.
    """
    from harness import LOGIN_PATH, start

    sharp_s = await seed_tenant(session_factory, domain="straße.de", name="Strasse Sharp S")
    plain = await seed_tenant(session_factory, domain="strasse.de", name="Strasse Plain")
    await seed_user(session_factory, sharp_s, email="admin@straße.de", password=_PASSWORD)
    await seed_user(session_factory, plain, email="admin@strasse.de", password=_OTHER_PASSWORD)

    outcome, status = await _run(session_factory, domain="STRAẞE.DE", email="root@straße.de")
    assert outcome.result == BootstrapResult.SUCCESS
    assert status == 0
    tenants = await _rows(session_factory, Tenant)
    assert len(tenants) == 2
    root = next(u for u in await _rows(session_factory, User) if u.email == "root@straße.de")
    assert root.tenant_id == sharp_s.id

    harness = await start(session_factory)
    try:
        for email, password in (
            ("admin@straße.de", _PASSWORD),
            ("admin@STRAẞE.DE", _PASSWORD),
            ("admin@strasse.de", _OTHER_PASSWORD),
            ("admin@STRASSE.DE", _OTHER_PASSWORD),
        ):
            login = await harness.http.post(LOGIN_PATH, json={"email": email, "password": password})
            assert login.status_code == 200, (email, login.text)
        # Each spelling refuses the other tenant's password: it resolved to one
        # tenant, and that tenant is the one its own domain folds to.
        for email, password in (
            ("admin@straße.de", _OTHER_PASSWORD),
            ("admin@strasse.de", _PASSWORD),
        ):
            login = await harness.http.post(LOGIN_PATH, json={"email": email, "password": password})
            assert login.status_code == 401, (email, login.text)
            assert error_code(login) == "INVALID_CREDENTIALS"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_domain_input_with_at_sign_fails_distinctly(session_factory) -> None:  # type: ignore[no-untyped-def]
    """C-119: the input is the domain itself, never an address to parse."""
    outcome, status = await _run(session_factory, domain="admin@acme.de")
    assert outcome.result == BootstrapResult.FAILED
    assert status != 0
    assert "@" in outcome.reason
    assert "AMBIGUOUS" not in outcome.reason
    assert await _snapshot(session_factory) == (0, 0, 0)


@pytest.mark.asyncio
async def test_inactive_target_tenant_fails_without_writes(session_factory) -> None:  # type: ignore[no-untyped-def]
    """C-120 ③: a non-active target fails, and nothing changes at all."""
    tenant = await seed_tenant(session_factory, domain=_DOMAIN, name=_NAME)
    async with session_factory() as session:
        loaded = await session.scalar(select(Tenant).where(Tenant.id == tenant.id))
        assert loaded is not None
        loaded.status = "suspended"
        await session.commit()

    outcome, status = await _run(session_factory)
    assert outcome.result == BootstrapResult.FAILED
    assert status != 0
    assert "active" in outcome.reason

    tenants = await _rows(session_factory, Tenant)
    assert len(tenants) == 1
    assert tenants[0].status == "suspended"
    assert await _rows(session_factory, User) == []
    assert await _rows(session_factory, AuditLog) == []


@pytest.mark.asyncio
async def test_email_outside_domain_fails_without_writes(session_factory) -> None:  # type: ignore[no-untyped-def]
    """An address the local login could never resolve to the tenant is refused."""
    outcome, status = await _run(session_factory, email="admin@other.com")
    assert outcome.result == BootstrapResult.FAILED
    assert status != 0
    assert "tenant domain" in outcome.reason

    assert await _snapshot(session_factory) == (0, 0, 0)


@pytest.mark.asyncio
async def test_occupied_email_fails_without_writes(session_factory) -> None:  # type: ignore[no-untyped-def]
    """C-114: an email already held by any account in the tenant is refused."""
    tenant = await seed_tenant(session_factory, domain=_DOMAIN, name=_NAME)
    await seed_user(
        session_factory,
        tenant,
        email=_EMAIL,
        name="Existing Member",
        password=_PASSWORD,
    )

    outcome, status = await _run(session_factory)
    assert outcome.result == BootstrapResult.FAILED
    assert status != 0
    assert "already" in outcome.reason

    users = await _rows(session_factory, User)
    assert len(users) == 1
    assert users[0].email == _EMAIL
    assert users[0].role == "employee"
    assert users[0].account_source == ACCOUNT_SOURCE_LOCAL
    assert verify_password(_PASSWORD, users[0].password_hash or "")


@pytest.mark.asyncio
async def test_short_password_fails_without_writes(session_factory) -> None:  # type: ignore[no-untyped-def]
    """C-120 ④: a password below the format floor fails the run, no partial write."""
    outcome, status = await _run(session_factory, password=_SHORT_PASSWORD)
    assert outcome.result == BootstrapResult.FAILED
    assert status != 0
    assert "8" in outcome.reason

    assert await _snapshot(session_factory) == (0, 0, 0)


@pytest.mark.asyncio
async def test_closed_capability_yields_incomplete_result(session_factory) -> None:  # type: ignore[no-untyped-def]
    """C-120 ②: capability off → incomplete; tenant/domain still become ready."""
    outcome, status = await _run(session_factory, local_login_enabled=False)
    assert outcome.result == BootstrapResult.INCOMPLETE
    assert status != 0

    tenants = await _rows(session_factory, Tenant)
    assert len(tenants) == 1
    assert tenants[0].sso_domain == _DOMAIN
    assert tenants[0].status == "active"
    assert await _rows(session_factory, User) == []
    audits = await _rows(session_factory, AuditLog)
    assert len(audits) == 1
    assert audits[0].action == ACTION_TENANT_CREATED


@pytest.mark.asyncio
async def test_four_results_are_mutually_distinguishable(session_factory) -> None:  # type: ignore[no-untyped-def]
    """success / skipped / incomplete / failed differ in name, exit code and writes."""
    # C-120 judges ① (any admin) before ② (capability), so the closed-capability
    # run must happen before an administrator exists.
    incomplete, incomplete_status = await _run(session_factory, local_login_enabled=False)
    assert incomplete.result == BootstrapResult.INCOMPLETE and incomplete_status != 0
    assert len(await _rows(session_factory, Tenant)) == 1

    ok, ok_status = await _run(session_factory)  # tenant exists, admin created
    assert ok.result == BootstrapResult.SUCCESS and ok_status == 0

    skip, skip_status = await _run(session_factory)  # admin exists now
    assert skip.result == BootstrapResult.SKIPPED and skip_status == 0

    # C-119's folded-domain ambiguity is judged before C-120, so it still fails
    # now that an administrator exists.
    await seed_tenant(session_factory, domain="ACME.COM", name="Acme Two")
    failed, failed_status = await _run(session_factory)
    assert failed.result == BootstrapResult.FAILED and failed_status != 0

    names = {ok.result, skip.result, incomplete.result, failed.result}
    codes = {ok_status, skip_status, incomplete_status, failed_status}
    assert len(names) == 4
    assert len(codes) == 2  # 0 for success/skip, 1 for incomplete/failure (C-121)
