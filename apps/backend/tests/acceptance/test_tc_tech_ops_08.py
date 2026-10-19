"""TC-tech-ops-08 — enable switch, template defaults and first-admin bootstrap.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-ops-08 → AC-T16) over
``docs/02-contracts.md`` C-110 / C-117 / C-88 and ``docs/01-scope.md`` F9. The
deployment surface is judged where it lives: the compose files and the
environment template are read as files, the read point is answered by a running
app, and the bootstrap runs against PostgreSQL.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from harness import CAPABILITY_PATH, seed_admin, seed_tenant, start
from sqlalchemy import func, select
from sqlalchemy.orm import noload

from app.auth.password import verify_password
from app.bootstrap import seed_local_family
from app.config import Settings
from app.models.tenant import Tenant
from app.models.user import ACCOUNT_SOURCE_LOCAL, ACCOUNT_SOURCE_SSO, User

TC_ID = "TC-tech-ops-08"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_COMPOSE_LOCAL = _REPO_ROOT / "docker-compose.yml"
_COMPOSE_QA = _REPO_ROOT / "docker-compose.qa.yml"
_COMPOSE_PROD = _REPO_ROOT / "docker-compose.prod.yml"
_ENV_TEMPLATE = _REPO_ROOT / ".env.example"
_DEPLOY_DIR = _REPO_ROOT / "deploy"

_DOMAIN = "acme.com"
_ADMIN_EMAIL = "admin@acme.com"
_ALICE_EMAIL = "alice@acme.com"
_PASSWORD = "Local-Admin-1"
_ENABLED_VALUE = re.compile(r"LOCAL_LOGIN_ENABLED\s*[:=]\s*[\"']?(true|1|yes|on)\b", re.IGNORECASE)


def _service_block(path: Path, service: str) -> str:
    """The YAML block of a top-level two-space ``service`` key."""
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"^  {re.escape(service)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_.-]+:|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{service} not found in {path}"
    return match.group("body")


def _bootstrap_settings(*, enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        local_login_enabled=enabled,
        local_admin_email=_ADMIN_EMAIL,
        local_admin_password=_PASSWORD,
    )


async def _run_bootstrap(session_factory: Any, *, enabled: bool) -> None:
    async with session_factory() as session:
        await seed_local_family(session, _bootstrap_settings(enabled=enabled))


async def _seeded_users(session_factory: Any) -> list[User]:
    async with session_factory() as session:
        rows = await session.execute(select(User).options(noload("*")))
        return list(rows.scalars().all())


# ── ① capability switch: declared on, undeclared off (C-110) ────────────────


def test_undeclared_deployment_reads_as_closed() -> None:
    """The capability has two states and an undeclared deployment gets the off one."""
    assert Settings(_env_file=None).local_login_enabled is False


@pytest.mark.asyncio
async def test_capability_read_point_reports_both_states(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The public read point needs no authentication and answers either state."""
    declared = await start(session_factory, local_login_enabled=True)
    undeclared = await start(session_factory, local_login_enabled=False)
    try:
        enabled = await declared.http.get(CAPABILITY_PATH)
        disabled = await undeclared.http.get(CAPABILITY_PATH)
        assert enabled.status_code == 200, enabled.text
        assert enabled.json() == {"data": {"localLoginEnabled": True}}
        assert disabled.status_code == 200, disabled.text
        assert disabled.json() == {"data": {"localLoginEnabled": False}}
    finally:
        await declared.close()
        await undeclared.close()


# ── ② orchestration values and template defaults (C-110 / F9) ───────────────


def test_local_family_orchestration_declares_the_capability_on() -> None:
    """Both local-family composes enable it explicitly, for backend and bootstrap."""
    for path in (_COMPOSE_LOCAL, _COMPOSE_QA):
        for service in ("backend", "db-seed"):
            block = _service_block(path, service)
            assert re.search(r"LOCAL_LOGIN_ENABLED:\s*[\"']?true", block), (path, service)


def test_local_family_bootstrap_is_a_one_shot_running_the_seed() -> None:
    """The capability travels with the existing one-shot container, not a new one."""
    for path in (_COMPOSE_LOCAL, _COMPOSE_QA):
        block = _service_block(path, "db-seed")
        assert "app.bootstrap" in block, path
        assert re.search(r"restart:\s*[\"']?no[\"']?\s*$", block, re.MULTILINE), path


def test_production_shape_never_enables_the_capability() -> None:
    """The production template is closed and contains no local-account seeding."""
    production = _COMPOSE_PROD.read_text(encoding="utf-8")
    assert not _ENABLED_VALUE.search(production)
    assert re.search(r"LOCAL_LOGIN_ENABLED:\s*\$\{LOCAL_LOGIN_ENABLED:-false\}", production)
    assert "app.bootstrap" not in production
    assert _service_block(_COMPOSE_PROD, "backend")  # template still declares the app

    template = _ENV_TEMPLATE.read_text(encoding="utf-8")
    assert not _ENABLED_VALUE.search(template)
    assert re.search(r"^LOCAL_LOGIN_ENABLED=false$", template, re.MULTILINE)

    for path in _DEPLOY_DIR.rglob("*"):
        if path.is_file() and path.suffix != ".md":
            content = path.read_text(encoding="utf-8", errors="ignore")
            assert not _ENABLED_VALUE.search(content), path


# ── ③ first platform administrator bootstrap (C-117 / C-88) ────────────────


@pytest.mark.asyncio
async def test_bootstrap_seeds_the_first_admin_when_enabled(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Enabled: tenant and identities seed, plus a local admin that must change."""
    await _run_bootstrap(session_factory, enabled=True)
    users = {user.email: user for user in await _seeded_users(session_factory)}

    alice = users[_ALICE_EMAIL]
    assert alice.role == "agent_admin"
    assert alice.account_source == ACCOUNT_SOURCE_SSO

    admin = users[_ADMIN_EMAIL]
    assert admin.role == "platform_admin"
    assert admin.account_source == ACCOUNT_SOURCE_LOCAL
    assert admin.must_change_password is True
    assert admin.sso_sub.startswith("local:")
    assert admin.password_hash is not None
    assert verify_password(_PASSWORD, admin.password_hash)


@pytest.mark.asyncio
async def test_bootstrap_leaves_the_content_unchanged_when_disabled(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Disabled: exactly the tenant and employee identity the SQL seed produced."""
    await _run_bootstrap(session_factory, enabled=False)
    users = await _seeded_users(session_factory)

    assert [user.email for user in users] == [_ALICE_EMAIL]
    assert not [user for user in users if user.role == "platform_admin"]
    assert not [user for user in users if user.account_source == ACCOUNT_SOURCE_LOCAL]


@pytest.mark.asyncio
async def test_bootstrap_does_not_create_when_a_platform_admin_exists(session_factory) -> None:  # type: ignore[no-untyped-def]
    """An existing platform administrator stops the bootstrap from adding one."""
    tenant = await seed_tenant(session_factory, domain=_DOMAIN, name="Acme")
    existing = await seed_admin(session_factory, tenant, email="boss@acme.com")

    await _run_bootstrap(session_factory, enabled=True)
    users = await _seeded_users(session_factory)

    admins = [user for user in users if user.role == "platform_admin"]
    assert [user.id for user in admins] == [existing.id]
    assert not [user for user in users if user.account_source == ACCOUNT_SOURCE_LOCAL]


@pytest.mark.asyncio
async def test_bootstrap_rejects_an_admin_outside_the_seeded_tenant_domain(session_factory) -> None:  # type: ignore[no-untyped-def]
    """An address local login cannot resolve back to the tenant fails loudly."""
    settings = Settings(
        _env_file=None,
        local_login_enabled=True,
        local_admin_email="admin@example.com",
        local_admin_password=_PASSWORD,
    )

    with pytest.raises(RuntimeError, match="tenant domain"):
        async with session_factory() as session:
            await seed_local_family(session, settings)

    assert await _seeded_users(session_factory) == []
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 0


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent_and_concurrency_safe(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Empty-DB concurrent runs converge on one tenant, one admin and two users."""
    settings = _bootstrap_settings(enabled=True)

    async def once() -> None:
        async with session_factory() as session:
            await seed_local_family(session, settings)

    async def assert_seeded_once() -> None:
        async with session_factory() as session:
            tenants = list(
                (await session.execute(select(Tenant).options(noload("*")))).scalars().all()
            )
        assert [tenant.sso_domain for tenant in tenants] == [_DOMAIN]
        users = await _seeded_users(session_factory)
        assert len(users) == 2
        assert sorted(user.email for user in users) == sorted([_ALICE_EMAIL, _ADMIN_EMAIL])
        assert len([user for user in users if user.role == "platform_admin"]) == 1

    await asyncio.gather(once(), once(), once())
    await assert_seeded_once()

    await once()
    await assert_seeded_once()
