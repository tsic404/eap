"""Serialized Alembic migration runner for container startup.

Every backend replica runs this at startup. The migration scripts are not
idempotent (bare create_table / create_foreign_key), so concurrent replicas
migrating a fresh database would collide on duplicate objects and crash-loop.
A session-level Postgres advisory lock serializes the step: one replica
migrates while the others block, then proceed once the schema is at head.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings

# Session-scoped lock key, distinct from the seed lock (app/seed.py uses
# hashtext('eap:seed')); session scope outlives the Alembic subprocess.
_MIGRATE_LOCK_SQL = "SELECT pg_advisory_lock(hashtext('eap:migrate'))"
_MIGRATE_UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtext('eap:migrate'))"


async def _run() -> int:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text(_MIGRATE_LOCK_SQL))
            await conn.commit()
            try:
                completed = await asyncio.to_thread(
                    subprocess.run, ["alembic", "upgrade", "head"], check=False
                )
                return completed.returncode
            finally:
                await conn.execute(text(_MIGRATE_UNLOCK_SQL))
                await conn.commit()
    finally:
        await engine.dispose()


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
