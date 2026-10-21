"""TC-tech-ops-09 — production bootstrap on an empty database: first local admin.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-ops-09 → AC-T18) over
``docs/02-contracts.md`` C-119 / C-120 / C-121 (and C-114 / C-113 observable
facts). The bootstrap action itself is driven as a module against PostgreSQL,
then the running ASGI stack proves the login journey the contract promises: the
initial password signs in, forces a change, and the new password re-admits the
account while the old one is rejected.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from harness import (
    CHANGE_PASSWORD_PATH,
    LOGIN_PATH,
    REFRESH_PATH,
    refresh_cookie,
    start,
)
from sqlalchemy import select
from sqlalchemy.orm import noload

from app.auth.password import verify_password
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.user import ACCOUNT_SOURCE_LOCAL, User
from app.production_bootstrap import (
    ACTION_ADMIN_CREATED,
    ACTION_TENANT_CREATED,
    BootstrapResult,
    bootstrap,
    build_parser,
    exit_status,
    resolve_admin_password,
)

TC_ID = "TC-tech-ops-09"

_TENANT_NAME = "Acme"
_TENANT_DOMAIN = "acme.com"
_ADMIN_EMAIL = "admin@acme.com"
_ADMIN_NAME = "Platform Admin"
_PASSWORD = "initial-bootstrap-pw-01"
_NEW_PASSWORD = "post-change-pw-02"
_SHORT_PASSWORD = "seven77"


async def _run(
    session_factory: Any,
    *,
    tenant_name: str = _TENANT_NAME,
    tenant_domain: str = _TENANT_DOMAIN,
    admin_email: str = _ADMIN_EMAIL,
    admin_name: str = _ADMIN_NAME,
    admin_password: str = _PASSWORD,
    local_login_enabled: bool = True,
) -> tuple[Any, int]:
    async with session_factory() as session:
        outcome = await bootstrap(
            session,
            tenant_name=tenant_name,
            tenant_domain=tenant_domain,
            admin_email=admin_email,
            admin_name=admin_name,
            admin_password=admin_password,
            local_login_enabled=local_login_enabled,
        )
        return outcome, exit_status(outcome.result)


async def _rows(session_factory: Any, model: Any) -> list[Any]:
    async with session_factory() as session:
        rows = await session.execute(select(model).options(noload("*")))
        return list(rows.scalars().all())


async def _sign_in(http: httpx.AsyncClient, *, email: str, password: str) -> httpx.Response:
    return await http.post(LOGIN_PATH, json={"email": email, "password": password})


async def _refresh(http: httpx.AsyncClient, cookie: str) -> httpx.Response:
    return await http.post(REFRESH_PATH, headers={"Cookie": f"refresh_token={cookie}"})


@pytest.mark.asyncio
async def test_empty_db_bootstrap_creates_tenant_and_first_admin(session_factory) -> None:  # type: ignore[no-untyped-def]
    """One run on an empty database: active tenant, local admin, audits, exit 0."""
    outcome, status = await _run(session_factory)
    assert outcome.result == BootstrapResult.SUCCESS
    assert status == 0

    tenants = await _rows(session_factory, Tenant)
    assert len(tenants) == 1
    tenant = tenants[0]
    assert tenant.sso_domain == _TENANT_DOMAIN
    assert tenant.status == "active"

    users = await _rows(session_factory, User)
    assert len(users) == 1
    admin = users[0]
    assert admin.tenant_id == tenant.id
    assert admin.email == _ADMIN_EMAIL
    assert admin.role == "platform_admin"
    assert admin.status == "active"
    assert admin.account_source == ACCOUNT_SOURCE_LOCAL
    assert admin.must_change_password is True
    assert admin.sso_sub.startswith("local:")
    assert admin.password_hash is not None
    assert verify_password(_PASSWORD, admin.password_hash)

    audits = await _rows(session_factory, AuditLog)
    actions = {audit.action for audit in audits}
    assert actions == {ACTION_TENANT_CREATED, ACTION_ADMIN_CREATED}
    assert all(audit.user_id is None for audit in audits)


@pytest.mark.asyncio
async def test_initial_password_logs_in_and_is_forced_to_change(session_factory) -> None:  # type: ignore[no-untyped-def]
    """C-113 journey: initial login succeeds, business calls are gated, change works."""
    await _run(session_factory)
    harness = await start(session_factory)
    try:
        login = await _sign_in(harness.http, email=_ADMIN_EMAIL, password=_PASSWORD)
        assert login.status_code == 200, login.text
        assert login.json() == {"data": {"mustChangePassword": True}}

        refreshed = await _refresh(harness.http, refresh_cookie(login))
        assert refreshed.status_code == 200, refreshed.text
        token = refreshed.json()["data"]["accessToken"]

        blocked = await harness.http.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert blocked.status_code == 403, blocked.text
        assert blocked.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"

        changed = await harness.http.post(
            CHANGE_PASSWORD_PATH,
            json={"currentPassword": _PASSWORD, "newPassword": _NEW_PASSWORD},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert changed.status_code == 200, changed.text

        old = await _sign_in(harness.http, email=_ADMIN_EMAIL, password=_PASSWORD)
        assert old.status_code == 401, old.text

        again = await _sign_in(harness.http, email=_ADMIN_EMAIL, password=_NEW_PASSWORD)
        assert again.status_code == 200, again.text
        assert again.json() == {"data": {"mustChangePassword": False}}
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_short_password_fails_without_writes(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A password below the format floor fails the run before any write (C-120 ④)."""
    outcome, status = await _run(session_factory, admin_password=_SHORT_PASSWORD)
    assert outcome.result == BootstrapResult.FAILED
    assert status != 0
    assert "8" in outcome.reason or "characters" in outcome.reason
    assert await _rows(session_factory, Tenant) == []
    assert await _rows(session_factory, User) == []
    assert await _rows(session_factory, AuditLog) == []


@pytest.mark.asyncio
async def test_concurrent_empty_db_runs_converge(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Parallel runs of the same input leave one tenant, one admin, one audit set."""
    outcomes = await asyncio.gather(*[_run(session_factory) for _ in range(3)])
    results = [outcome.result for outcome, _ in outcomes]
    assert results.count(BootstrapResult.SUCCESS) == 1
    assert all(r in (BootstrapResult.SUCCESS, BootstrapResult.SKIPPED) for r in results)

    tenants = await _rows(session_factory, Tenant)
    assert len(tenants) == 1
    users = await _rows(session_factory, User)
    assert len(users) == 1
    audits = await _rows(session_factory, AuditLog)
    assert len(audits) == 2
    assert {audit.action for audit in audits} == {ACTION_TENANT_CREATED, ACTION_ADMIN_CREATED}


def test_initial_password_has_channels_that_stay_out_of_argv() -> None:
    """Stdin and env inputs keep the password out of shell history and ``ps``."""
    import io

    stdin = io.StringIO("piped-secret-123\n")
    args = build_parser().parse_args(
        [
            "Acme",
            "acme.com",
            "--admin-email",
            "admin@acme.com",
            "--admin-name",
            "Platform Admin",
            "--admin-password-stdin",
        ]
    )
    assert args.admin_password is None  # never on the command line
    assert (
        resolve_admin_password(
            inline=args.admin_password,
            env_var=args.admin_password_env,
            use_stdin=args.admin_password_stdin,
            environ={},
            stdin=stdin,
        )
        == "piped-secret-123"
    )

    env_args = build_parser().parse_args(
        ["Acme", "acme.com", "--admin-password-env", "EAP_BOOTSTRAP_PW"]
    )
    assert (
        resolve_admin_password(
            inline=env_args.admin_password,
            env_var=env_args.admin_password_env,
            use_stdin=env_args.admin_password_stdin,
            environ={"EAP_BOOTSTRAP_PW": "env-secret-456"},
            stdin=io.StringIO(""),
        )
        == "env-secret-456"
    )

    for channel in (
        {"inline": "a-password", "env_var": "X", "use_stdin": False},
        {"inline": "a-password", "env_var": None, "use_stdin": True},
    ):
        with pytest.raises(ValueError):
            resolve_admin_password(
                environ={"X": "b-password"}, stdin=io.StringIO("c-password\n"), **channel
            )
