"""Health check endpoints (architecture doc §25).

Probes are raw (unwrapped) responses — the shared API contract documents this as
the explicit exception to the ``{"data": …}`` envelope so orchestrators and load
balancers can consume them directly.
"""

import asyncio

import asyncpg  # type: ignore[import-untyped]
import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import Settings

router = APIRouter(tags=["health"])

_PING_TIMEOUT_SECONDS = 2.0


def _postgres_dsn(database_url: str) -> str:
    # asyncpg consumes postgresql:// DSNs; the SQLAlchemy driver suffix is dropped.
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


async def _ping_database(database_url: str) -> bool:
    try:
        conn = await asyncpg.connect(dsn=_postgres_dsn(database_url), timeout=_PING_TIMEOUT_SECONDS)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
        return True
    except Exception:
        return False


async def _ping_redis(redis_url: str) -> bool:
    try:
        client = aioredis.from_url(
            redis_url,
            socket_connect_timeout=_PING_TIMEOUT_SECONDS,
            socket_timeout=_PING_TIMEOUT_SECONDS,
        )
        try:
            return bool(await client.ping())
        finally:
            await client.aclose()
    except Exception:
        return False


async def _ping_dify(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=_PING_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/health")
            return resp.status_code == 200
    except Exception:
        return False


@router.get("/api/health")
async def health(request: Request) -> dict[str, str]:
    """Basic service info."""
    settings: Settings = request.app.state.settings
    return {"service": "eap-backend", "version": settings.app_version, "status": "ok"}


@router.get("/api/health/live")
async def health_live() -> dict[str, str]:
    """Liveness probe — returns 200 while the process is up."""
    return {"status": "ok"}


@router.get("/api/health/ready")
async def health_ready(request: Request) -> JSONResponse:
    """Readiness probe — reports dependency (DB / Redis / Dify) reachability."""
    settings: Settings = request.app.state.settings
    database_ok, redis_ok, dify_ok = await asyncio.gather(
        _ping_database(settings.database_url),
        _ping_redis(settings.redis_url),
        _ping_dify(settings.dify_api_base_url),
    )
    checks = {
        "database": "ok" if database_ok else "error",
        "redis": "ok" if redis_ok else "error",
        "dify": "ok" if dify_ok else "error",
    }
    healthy = database_ok and redis_ok and dify_ok
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
