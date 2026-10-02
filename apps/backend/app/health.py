"""Health check endpoints (architecture doc §25).

Probes are raw (unwrapped) responses — the shared API contract documents this as
the explicit exception to the ``{"data": …}`` envelope so orchestrators and load
balancers can consume them directly.
"""

import asyncio
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.dify_console import DifyConsoleClient, DifyConsoleError

router = APIRouter(tags=["health"])

_PING_TIMEOUT_SECONDS = 2.0
# The console session login plus provider listing is heavier than a raw /health
# ping, but a readiness probe must still never block on a stalled Dify.
_PROVIDER_TIMEOUT_SECONDS = 5.0


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


def _provider_entries(raw: Any) -> list[Any]:
    # Dify answers with ``{"data": [...]}``; a bare list keeps older payloads working.
    entries = raw.get("data") if isinstance(raw, dict) else raw
    return entries if isinstance(entries, list) else []


def _is_configured_provider(entry: Any) -> bool:
    """Whether one provider entry carries credentials that can serve models.

    Dify lists every model provider plugin the workspace has installed,
    configured or not. An entry still in ``no_configure`` state whose hosted
    ``system_configuration`` is disabled cannot serve a single request, so its
    presence alone must not satisfy the readiness probe.
    """
    if not isinstance(entry, dict):
        return False
    custom = entry.get("custom_configuration")
    if isinstance(custom, dict):
        if custom.get("status") == "active":
            return True
        # Older payloads report credentials through the model lists instead.
        if custom.get("custom_models") or custom.get("models"):
            return True
    system = entry.get("system_configuration")
    return bool(isinstance(system, dict) and system.get("enabled"))


async def _dify_has_model_provider(console: DifyConsoleClient | None) -> bool:
    """Whether the Dify workspace has at least one configured model provider.

    Reachability alone is not readiness: a workspace whose providers all lack
    credentials accepts connections and then fails every chat and
    knowledge-base call. Console credentials are part of the deployment
    contract — when they are missing, rejected, or the listing times out, the
    providers cannot be verified and the probe stays negative instead of
    reporting a false ``ok``.
    """
    if console is None:
        return False
    try:
        async with asyncio.timeout(_PROVIDER_TIMEOUT_SECONDS):
            raw = await console.get_model_providers()
    except (DifyConsoleError, httpx.HTTPError, TimeoutError):
        return False
    return any(_is_configured_provider(entry) for entry in _provider_entries(raw))


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
    """Readiness probe — reports DB / Redis / Dify health.

    Dify counts as healthy only when it is reachable *and* its workspace has a
    configured model provider (see ``_dify_has_model_provider``).
    """
    settings: Settings = request.app.state.settings
    console: DifyConsoleClient | None = getattr(request.app.state, "dify_console", None)
    database_ok, redis_ok, dify_ok = await asyncio.gather(
        _ping_database(settings.database_url),
        _ping_redis(settings.redis_url),
        _ping_dify(settings.dify_api_base_url),
    )
    # The provider listing needs a reachable Dify: skip it otherwise rather than
    # paying for a doomed console login on every probe.
    dify_healthy = dify_ok and await _dify_has_model_provider(console)
    checks = {
        "database": "ok" if database_ok else "error",
        "redis": "ok" if redis_ok else "error",
        "dify": "ok" if dify_healthy else "error",
    }
    healthy = database_ok and redis_ok and dify_healthy
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
