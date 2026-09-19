"""Testcontainers-backed integration fixtures (architecture doc §31.1).

PostgreSQL (pgvector) and Redis run as real containers; the Dify boundary stays
mocked because the issue scope only containers the data stores. Test data is
built through factory helpers with per-worker tenant isolation, and every test
runs inside a transaction that is rolled back at teardown (sessions join the
outer transaction via savepoints, so handler ``session.commit()`` calls never
escape it).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import httpx
import jwt as pyjwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from redis import Redis
from rq import Queue
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

import app.models  # noqa: F401  # registers every model on Base.metadata
from app.config import Settings
from app.db import get_session
from app.dify_console import DifyConsoleClient
from app.main import create_app
from app.models.agent import AgentRegistry
from app.models.base import Base
from app.models.knowledge import KnowledgeBaseRegistry
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.tool import ToolRegistry
from app.models.user import User
from app.services import task_service as task_service_module
from app.services.conversation import ConversationService
from app.services.knowledge import KnowledgeService
from app.services.tool_proxy import ToolProxy

Role = str


# ── process/worker isolation ────────────────────────────────────────────────


@pytest.fixture(scope="session")
def worker_index(request: pytest.FixtureRequest) -> str:
    """Unique per-worker token so parallel workers never share tenant slugs."""
    workerinput = getattr(request.config, "workerinput", None)
    if workerinput:
        return str(workerinput.get("workerid", "master"))
    return f"pid{os.getpid()}"


# ── containers ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("pgvector/pgvector:pg16") as container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    # testcontainers emits a psycopg2 DSN; SQLAlchemy async needs asyncpg.
    url = make_url(postgres_container.get_connection_url())
    return url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def redis_url(redis_container: RedisContainer) -> str:
    return f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}/0"


@pytest.fixture(scope="session")
def keypair() -> tuple[rsa.RSAPrivateKey, str]:
    """One RS256 keypair per session; access tokens are signed with it."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_key, public_pem


@pytest.fixture(scope="session")
def integration_settings(
    database_url: str, redis_url: str, keypair: tuple[rsa.RSAPrivateKey, str]
) -> Settings:
    _, public_pem = keypair
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=database_url,
        redis_url=redis_url,
        jwt_public_key=public_pem,
        rate_limit_enabled=False,
    )


@pytest_asyncio.fixture(scope="session")
async def schema_ready(integration_settings: Settings) -> str:
    """Create the full schema once and commit it; yield only the URL string.

    The engine is created and disposed inside this fixture so nothing
    loop-bound escapes into function-scoped tests (pytest-asyncio runs each test
    on its own event loop).
    """
    engine = create_async_engine(integration_settings.database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()
    return integration_settings.database_url


# ── transaction-rollback handle ─────────────────────────────────────────────


@dataclass
class Db:
    """One connection with an open outer transaction for a single test.

    Sessions produced by :meth:`session` join the outer transaction through a
    savepoint; their ``commit()`` only releases that savepoint, so the teardown
    ``rollback()`` discards every write the test made.
    """

    conn: AsyncConnection

    def session(self) -> Any:
        return AsyncSession(
            bind=self.conn,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )


@pytest_asyncio.fixture
async def db(schema_ready: str) -> AsyncIterator[Db]:
    engine = create_async_engine(schema_ready)
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            yield Db(conn)
        finally:
            await trans.rollback()
    await engine.dispose()


# ── factory helpers (architecture doc §31.1.4) ──────────────────────────────


async def seed_tenant_user(
    session: AsyncSession,
    *,
    role: Role = "agent_admin",
    worker_index: str = "master",
    suffix: str | None = None,
) -> tuple[Tenant, User]:
    """Create an isolated tenant + user; the slug is prefixed per worker."""
    slug = f"tenant-test-{worker_index}-{suffix or uuid.uuid4().hex[:8]}"
    tenant = Tenant(name="Test", slug=slug, sso_provider="local", status="active")
    session.add(tenant)
    await session.flush()
    user = User(
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        name="Test User",
        role=role,
        status="active",
    )
    session.add(user)
    await session.commit()
    return tenant, user


async def seed_user(
    session: AsyncSession,
    tenant: Tenant,
    *,
    role: Role = "employee",
) -> User:
    """Create an additional user in an existing tenant (same-tenant isolation)."""
    user = User(
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        name="Test User",
        role=role,
        status="active",
    )
    session.add(user)
    await session.commit()
    return user


async def seed_agent(
    session: AsyncSession,
    tenant: Tenant,
    *,
    agent_id: str,
    status: str = "published",
    created_by: uuid.UUID | None = None,
) -> AgentRegistry:
    agent = AgentRegistry(
        agent_id=agent_id,
        tenant_id=tenant.id,
        dify_app_id=f"dify-{agent_id}",
        dify_api_key="app-secret",
        name=agent_id,
        type="chat",
        status=status,
        version=1,
        created_by=created_by,
    )
    session.add(agent)
    await session.commit()
    return agent


async def seed_kb(
    session: AsyncSession,
    tenant: Tenant,
    *,
    kb_id: str = "kb-1",
) -> KnowledgeBaseRegistry:
    kb = KnowledgeBaseRegistry(
        kb_id=kb_id,
        tenant_id=tenant.id,
        dify_dataset_id=f"ds-{kb_id}",
        dify_api_key="ds-secret",
        name=kb_id,
        type="business",
        indexing_status="ready",
        doc_count=0,
        chunk_count=0,
    )
    session.add(kb)
    await session.commit()
    return kb


async def seed_tool(
    session: AsyncSession,
    tenant: Tenant,
    *,
    tool_id: str = "tool-1",
    permission_mode: str = "auto",
) -> ToolRegistry:
    tool = ToolRegistry(
        tool_id=tool_id,
        tenant_id=tenant.id,
        name=tool_id,
        type="http",
        endpoint="http://tool.example/api",
        method="POST",
        risk_level="medium",
        permission_mode=permission_mode,
        auth_type="none",
        timeout_ms=10000,
        retry_policy={"max_retries": 2, "base_delay_ms": 1000},
        status="active",
    )
    session.add(tool)
    await session.commit()
    return tool


async def seed_task(
    session: AsyncSession,
    tenant: Tenant,
    user: User,
    *,
    status: str = "pending",
    type_: str = "tool_approval",
) -> Task:
    task = Task(
        tenant_id=tenant.id,
        creator_id=user.id,
        type=type_,
        title="Approve tool",
        priority="high",
        status=status,
        payload={"tool_id": "tool-1", "params": {"a": 1}},
        max_retries=3,
    )
    session.add(task)
    await session.commit()
    return task


# ── Dify console mock ───────────────────────────────────────────────────────


def default_console() -> Any:
    """AsyncMock returning the fixtures every DifyConsoleClient call needs."""
    console = AsyncMock(spec=DifyConsoleClient)
    console.create_app.return_value = {"id": "dify-app-1", "name": "app"}
    console.configure_model.return_value = {"result": "success"}
    console.create_api_key.return_value = {"token": "app-secret-1"}
    console.delete_app.return_value = None
    console.create_dataset.return_value = {"id": "ds-1"}
    console.delete_dataset.return_value = None
    console.get_dataset_api_keys.return_value = {"data": [{"token": "ds-secret"}]}
    console.upload_file.return_value = {"id": "file-1"}
    console.create_document.return_value = {
        "documents": [{"id": "doc-1", "indexing_status": "indexing"}]
    }
    console.list_documents.return_value = {"data": [], "total": 0}
    console.get_document_indexing_status.return_value = {"indexing_status": "completed"}
    console.get_model_providers.return_value = {
        "data": [
            {
                "provider": "openai",
                "label": {"en_US": "OpenAI"},
                "preferred_provider_type": "custom",
                "custom_configuration": {"models": [{"deprecated": False}]},
            }
        ]
    }
    return console


# ── application + client ────────────────────────────────────────────────────


@dataclass
class Api:
    """Everything an integration test needs: client, app state, and factories."""

    client: httpx.AsyncClient
    app: Any
    db: Db
    settings: Settings
    private_key: rsa.RSAPrivateKey
    worker_index: str
    console: Any
    redis_conn: Redis
    enqueued: list[dict[str, str]] = field(default_factory=list)

    def sign(self, user: User, tenant: Tenant) -> str:
        return pyjwt.encode(
            {"sub": str(user.id), "tenantId": str(tenant.id), "role": user.role},
            self.private_key,
            algorithm="RS256",
        )

    def auth(self, user: User, tenant: Tenant) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.sign(user, tenant)}"}

    async def seed(
        self, role: Role = "agent_admin", *, suffix: str | None = None
    ) -> tuple[Tenant, User]:
        async with self.db.session() as session:
            return await seed_tenant_user(
                session, role=role, worker_index=self.worker_index, suffix=suffix
            )

    async def seed_user(self, tenant: Tenant, role: Role = "employee") -> User:
        async with self.db.session() as session:
            return await seed_user(session, tenant, role=role)

    async def seed_agent(
        self, tenant: Tenant, *, agent_id: str, status: str = "published"
    ) -> None:
        async with self.db.session() as session:
            await seed_agent(session, tenant, agent_id=agent_id, status=status)

    async def seed_kb(self, tenant: Tenant, *, kb_id: str = "kb-1") -> None:
        async with self.db.session() as session:
            await seed_kb(session, tenant, kb_id=kb_id)

    async def seed_tool(
        self, tenant: Tenant, *, tool_id: str = "tool-1", permission_mode: str = "auto"
    ) -> None:
        async with self.db.session() as session:
            await seed_tool(session, tenant, tool_id=tool_id, permission_mode=permission_mode)

    async def seed_task(
        self, tenant: Tenant, user: User, *, status: str = "pending", type_: str = "tool_approval"
    ) -> Task:
        async with self.db.session() as session:
            return await seed_task(session, tenant, user, status=status, type_=type_)


@pytest_asyncio.fixture
async def api(
    db: Db,
    integration_settings: Settings,
    keypair: tuple[rsa.RSAPrivateKey, str],
    worker_index: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Api]:
    private_key, _ = keypair
    app = create_app(integration_settings)
    console = default_console()
    app.state.dify_console = console
    app.state.knowledge_service = KnowledgeService(console, settings=integration_settings)
    app.state.conversation_service = ConversationService(settings=integration_settings)
    app.state.tool_proxy = ToolProxy(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))
    )

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with db.session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    # Route RQ enqueues at the testcontainers Redis so ``wait_for_job`` can
    # observe them; the queue module otherwise targets the global settings URL.
    redis_conn = Redis.from_url(integration_settings.redis_url)
    enqueued: list[dict[str, str]] = []

    def _enqueue(
        task_id: str, outbox_id: str, queue_name: str, payload: dict[str, Any], queue: Any = None
    ) -> None:
        del queue  # injectable seam unused here; enqueue straight to test Redis
        Queue(queue_name, connection=redis_conn).enqueue(
            "app.workers.process_task.process_task",
            task_id,
            outbox_id,
            payload,
            job_id=outbox_id,
        )
        enqueued.append({"task_id": task_id, "outbox_id": outbox_id, "queue_name": queue_name})

    monkeypatch.setattr(task_service_module, "enqueue_process_task", _enqueue)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Api(
                client=client,
                app=app,
                db=db,
                settings=integration_settings,
                private_key=private_key,
                worker_index=worker_index,
                console=console,
                redis_conn=redis_conn,
                enqueued=enqueued,
            )
    finally:
        # The httpx client closes via its context manager; the sync Redis client
        # has no async context manager, so release its pool explicitly.
        redis_conn.close()


# ── async-wait helpers (architecture doc §33.7.3) ───────────────────────────


@pytest.fixture
def wait_for_indexed() -> Callable[..., Any]:
    """Poll a document's indexing status until it reaches a terminal state."""

    async def _wait(
        client: httpx.AsyncClient,
        auth: dict[str, str],
        kb_id: str,
        document_id: str,
        *,
        timeout: float = 10.0,
        poll: float = 0.05,
    ) -> str:
        import asyncio
        import time

        deadline = time.monotonic() + timeout
        status = "indexing"
        while time.monotonic() < deadline:
            resp = await client.get(
                f"/api/knowledge-bases/{kb_id}/documents/{document_id}/status", headers=auth
            )
            status = resp.json()["data"]["status"]
            if status in {"completed", "failed"}:
                return status
            await asyncio.sleep(poll)
        return status

    return _wait


@pytest.fixture
def wait_for_job() -> Callable[..., Any]:
    """Poll an RQ queue with ``fetch_job`` until a job appears (no bare sleep)."""

    async def _wait(queue: Queue, job_id: str, *, timeout: float = 10.0, poll: float = 0.05) -> Any:
        import asyncio
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = queue.fetch_job(job_id)
            if job is not None:
                return job
            await asyncio.sleep(poll)
        return None

    return _wait
