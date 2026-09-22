"""Shared pytest fixtures."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  # registers every model on Base.metadata
from app.main import create_app
from app.models.base import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://eap:eap_password@localhost:5432/eap"
)


class FakeRedis:
    """In-memory stand-in for the Redis commands the app issues."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._ttls: dict[str, int | None] = {}

    async def set(
        self, name: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and name in self._data:
            return None
        self._data[name] = value
        self._ttls[name] = ex
        return True

    async def get(self, name: str) -> str | None:
        return self._data.get(name)

    async def getdel(self, name: str) -> str | None:
        self._ttls.pop(name, None)
        return self._data.pop(name, None)

    def recorded_ttl(self, name: str) -> int | None:
        """The expiry handed to ``SET EX``, so tests can assert the TTL policy."""
        return self._ttls.get(name)


def _drop_tables(conn: object) -> None:
    # Drop everything registered so a database that already carries the full
    # migrated schema is cleared in dependency order.
    Base.metadata.drop_all(conn, checkfirst=True)  # type: ignore[arg-type]


def _create_tables(conn: object) -> None:
    # Full schema: the ORM models use `lazy="selectin"` relationships, so loading
    # a Tenant or User eagerly pulls its relations (agent_registry, user_memories,
    # …). Those tables must exist or the load raises UndefinedTableError.
    Base.metadata.create_all(conn, checkfirst=True)  # type: ignore[arg-type]


@pytest_asyncio.fixture(scope="session")
async def test_database_url() -> AsyncIterator[str]:
    """Provision a process-unique database and drop it at session end.

    Concurrent pytest processes share ``TEST_DATABASE_URL``, so their schema
    setup and teardown race (two ``create_all`` calls building the same tables
    simultaneously). Each process instead creates its own throwaway database on
    the same server, so processes never touch each other's schema or rows.
    """
    base_url = make_url(TEST_DATABASE_URL)
    db_name = f"{base_url.database}_test_{uuid.uuid4().hex[:12]}"
    isolated_url = base_url.set(database=db_name)

    # CREATE/DROP DATABASE cannot run inside a transaction; AUTOCOMMIT executes
    # each statement directly against the shared maintenance database.
    admin_engine = create_async_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        try:
            yield isolated_url.render_as_string(hide_password=False)
        finally:
            async with admin_engine.connect() as conn:
                await conn.execute(text(f'DROP DATABASE "{db_name}" WITH (FORCE)'))
    finally:
        await admin_engine.dispose()


@pytest_asyncio.fixture
async def session_factory(test_database_url: str):
    """Yield a session factory against an isolated full-schema database."""
    engine = create_async_engine(test_database_url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(_drop_tables)
        await conn.run_sync(_create_tables)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_tables)
        await engine.dispose()


class EchoItem(BaseModel):
    name: str


@pytest.fixture
def client() -> Iterator[TestClient]:
    app: FastAPI = create_app()

    @app.get("/api/test/echo")
    def echo() -> dict[str, str]:
        return {"hello": "world"}

    @app.get("/api/test/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    @app.get("/api/test/missing")
    def missing() -> None:
        raise HTTPException(status_code=404, detail="resource not found")

    @app.post("/api/test/validate")
    def validate(item: EchoItem) -> EchoItem:
        return item

    # raise_server_exceptions=False so the global exception handlers' HTTP
    # responses are observable instead of being re-raised by the server error
    # middleware (which always re-raises after sending the 500 response).
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
