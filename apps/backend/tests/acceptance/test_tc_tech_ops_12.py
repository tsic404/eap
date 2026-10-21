"""TC-tech-ops-12 — standard production deployment stays at the zero-seed baseline.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-ops-12 → AC-T20, F7
negative) over ``docs/02-contracts.md`` C-119 / C-117 / C-110 and
``docs/01-scope.md`` F9. A standard production deployment that never runs the
deployment-time bootstrap holds no tenant (domained or not) and no platform
admin of any source; the app still answers its health and capability read
points, and no orchestration step seeds or auto-triggers the bootstrap.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from harness import CAPABILITY_PATH, start
from sqlalchemy import select
from sqlalchemy.orm import noload

from app.models.tenant import Tenant
from app.models.user import User

TC_ID = "TC-tech-ops-12"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_COMPOSE_PROD = _REPO_ROOT / "docker-compose.prod.yml"
_SEED_MODULE = _REPO_ROOT / "apps" / "backend" / "app" / "seed.py"


@pytest.mark.asyncio
async def test_empty_production_database_answers_its_read_points(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The backend boots and answers health/capability reads with no seed data."""
    harness = await start(session_factory, local_login_enabled=False)
    try:
        live = await harness.http.get("/api/health/live")
        assert live.status_code == 200, live.text

        health = await harness.http.get("/api/health")
        assert health.status_code == 200, health.text

        capability = await harness.http.get(CAPABILITY_PATH)
        assert capability.status_code == 200, capability.text
        assert capability.json() == {"data": {"localLoginEnabled": False}}
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_standard_deployment_holds_no_tenant_and_no_platform_admin(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The empty database the standard deployment leaves is the zero-seed baseline."""
    async with session_factory() as session:
        tenants = (await session.execute(select(Tenant).options(noload("*")))).scalars().all()
        users = (await session.execute(select(User).options(noload("*")))).scalars().all()

    assert tenants == []
    assert [user for user in users if user.role == "platform_admin"] == []
    assert users == []


def test_production_orchestration_does_not_seed_or_bootstrap() -> None:
    """No compose step creates tenants/accounts or triggers the deployment action."""
    production = _COMPOSE_PROD.read_text(encoding="utf-8")
    assert "app.bootstrap" not in production
    assert "app.production_bootstrap" not in production
    assert "app.seed" not in production
    # C-95: the production one-shot containers carry no tenant seeding, and the
    # capability stays closed by default (F9).
    assert "db-seed" not in production
    assert re.search(r"LOCAL_LOGIN_ENABLED:\s*\$\{LOCAL_LOGIN_ENABLED:-false\}", production)


def test_legacy_production_seed_module_is_gone() -> None:
    """The old ``app.seed`` path (default tenant + SSO admin) is not shipped (C-119)."""
    assert not _SEED_MODULE.exists(), "app/seed.py must not remain a production seed path"
