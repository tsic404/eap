"""Real-DB integration test for the tool CRUD PATCH regression.

Verity's QA caught a ``MissingGreenlet`` defect the stub-session tests hid:
``ToolService.update()`` returned the ORM row after ``commit()`` without
refreshing, so the ``onupdate=func.now()`` ``updated_at`` column was expired
and ``ToolRead.model_validate`` lazy-loaded it outside a greenlet. This test
drives the full ASGI stack against a real Postgres + asyncpg session — all in
one event loop — so the expiry path is actually exercised.

It creates and drops a dedicated database and skips when Postgres is
unreachable, so ``pytest`` still passes on a developer box without a DB.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  # register every model on Base.metadata
from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models.base import Base
from app.models.tenant import Tenant
from app.models.user import User
from app.services.tool_proxy import ToolProxy

_ADMIN_DSN = os.environ.get(
    "TEST_DB_ADMIN_DSN", "postgresql://eap:eap_password@localhost:5432/postgres"
)
_TEST_DSN = os.environ.get(
    "TEST_DB_DSN", "postgresql://eap:eap_password@localhost:5432/eap_tools_integration"
)
# A process-unique database name so concurrent pytest processes each get their
# own throwaway database instead of racing CREATE/DROP on a shared name — this
# holds for the default DSN and an explicit TEST_DB_DSN alike.
_base_db_name = make_url(_TEST_DSN).database
if not _base_db_name:
    raise ValueError("TEST_DB_DSN must include a database name")
_TEST_DB_NAME = f"{_base_db_name}_{uuid.uuid4().hex[:12]}"
_TEST_DSN = make_url(_TEST_DSN).set(database=_TEST_DB_NAME).render_as_string(hide_password=False)
_TEST_ASYNC_URL = _TEST_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)


def _postgres_available() -> bool:
    async def _check() -> bool:
        try:
            conn = await asyncpg.connect(_ADMIN_DSN, timeout=2)
            await conn.close()
            return True
        except Exception:
            return False

    return asyncio.run(_check())


pytestmark = pytest.mark.skipif(
    not _postgres_available(), reason="Postgres unavailable; skipping real-DB integration test"
)


def _keypair() -> tuple[object, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_key, public_pem


async def _create_database() -> None:
    admin = await asyncpg.connect(_ADMIN_DSN)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}"')
        await admin.execute(f'CREATE DATABASE "{_TEST_DB_NAME}"')
    finally:
        await admin.close()


async def _drop_database() -> None:
    admin = await asyncpg.connect(_ADMIN_DSN)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}"')
    finally:
        await admin.close()


@pytest.mark.asyncio
async def test_post_patch_patch_all_200() -> None:
    await _create_database()
    # NullPool keeps each asyncpg connection scoped to the single event loop this
    # test runs in, so the ASGI transport and the ORM share one loop.
    engine = create_async_engine(_TEST_ASYNC_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            await conn.run_sync(Base.metadata.create_all)

        async with factory() as session:
            tenant = Tenant(name="Tenant", slug="tenant-a", sso_provider="local", status="active")
            session.add(tenant)
            await session.flush()
            user = User(
                tenant_id=tenant.id,
                sso_sub="agent-admin-sub",
                email="admin@example.com",
                name="Admin",
                role="agent_admin",
                status="active",
            )
            session.add(user)
            await session.commit()
            tenant_id, user_id = str(tenant.id), str(user.id)

        private_key, public_pem = _keypair()
        token = pyjwt.encode(
            {"sub": user_id, "role": "agent_admin", "tenantId": tenant_id},
            private_key,
            algorithm="RS256",
        )

        async def _real_session() -> AsyncIterator[AsyncSession]:
            async with factory() as session:
                yield session

        app = create_app(
            Settings(_env_file=None, jwt_public_key=public_pem, rate_limit_enabled=False)
        )
        app.dependency_overrides[get_session] = _real_session
        app.state.tool_proxy = ToolProxy(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
        )

        transport = httpx.ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {token}"}
        body = {
            "name": "Contract Check",
            "tool_id": "qa-patch-2",
            "type": "http",
            "endpoint": "http://upstream.example/api",
            "risk_level": "low",
            "permission_mode": "auto",
        }
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/tools", json=body, headers=headers)
            assert resp.status_code == 201, resp.text

            resp = await client.get("/api/tools/qa-patch-2", headers=headers)
            assert resp.status_code == 200, resp.text

            resp = await client.patch(
                "/api/tools/qa-patch-2", json={"description": "d1"}, headers=headers
            )
            assert resp.status_code == 200, resp.text

            # The second PATCH is the regression trigger: the prior commit
            # expired the ``onupdate=func.now()`` ``updated_at`` column, so the
            # next read would lazy-load it outside a greenlet → 500.
            resp = await client.patch(
                "/api/tools/qa-patch-2", json={"description": "d2"}, headers=headers
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["description"] == "d2"
    finally:
        await engine.dispose()
        await _drop_database()
