"""EAP backend application entrypoint: app factory, lifespan, middleware pipeline."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.auth.oidc import OidcClient
from app.auth.refresh import RefreshService
from app.auth.routes import router as auth_router
from app.auth.state_store import OidcStateStore
from app.config import Settings, get_settings
from app.db import async_session_factory
from app.dify_console import DifyConsoleClient
from app.errors import register_exception_handlers
from app.events.audit import register_audit_log_handler
from app.events.run_log import register_run_log_handler
from app.health import router as health_router
from app.logging_conf import configure_logging, get_logger
from app.metrics import register_db_pool_metrics, register_rq_metrics
from app.middleware.request_context import RequestContextMiddleware
from app.middleware.security import (
    CORSMiddleware,
    HelmetMiddleware,
    JWTMiddleware,
    RateLimitMiddleware,
    RolesMiddleware,
    TenantMiddleware,
)
from app.middleware.transform import TransformMiddleware
from app.rate_limit import RedisTokenBucket
from app.routers.agents import router as agents_router
from app.routers.conversations import router as conversations_router
from app.routers.dashboard import router as dashboard_router
from app.routers.files import router as files_router
from app.routers.knowledge import router as knowledge_router
from app.routers.models import router as models_router
from app.routers.run_logs import router as run_logs_router
from app.routers.tasks import router as tasks_router
from app.routers.tools import router as tools_router
from app.services.conversation import ConversationService
from app.services.knowledge import KnowledgeService
from app.services.task_reconcile import create_scheduler
from app.services.tool_proxy import ToolProxy

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    log.info("startup", app_env=settings.app_env, version=settings.app_version)
    dify_console = DifyConsoleClient(settings)
    app.state.dify_console = dify_console
    app.state.knowledge_service = KnowledgeService(dify_console, settings=settings)
    app.state.conversation_service = ConversationService(
        settings=settings, tool_proxy=app.state.tool_proxy
    )
    await dify_console.startup()

    # Auth runtime: a shared Redis client (lazy — no connection until first use)
    # backs the OIDC state store; the OIDC client owns its httpx transport.
    redis = aioredis.from_url(settings.redis_url)
    oidc = OidcClient(settings)
    app.state.redis = redis
    app.state.oidc = oidc
    app.state.state_store = OidcStateStore(redis)
    app.state.refresh_service = RefreshService(settings)
    app.state.task_reconcile_scheduler.start()
    try:
        yield
    finally:
        app.state.task_reconcile_scheduler.shutdown(wait=False)
        await oidc.close()
        await redis.aclose()
        await dify_console.shutdown()
        await app.state.rate_limiter.aclose()
        await app.state.tool_proxy.aclose()
        log.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        description="Enterprise Agent Platform backend API",
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.rate_limiter = RedisTokenBucket(settings.redis_url)
    app.state.tool_proxy = ToolProxy()
    app.state.task_reconcile_scheduler = create_scheduler(async_session_factory)
    # add_middleware prepends, so the LAST added here is the OUTERMOST stage.
    # Execution order: RequestContext → Helmet → CORS → JWT → Tenant → RateLimit
    # → Roles → Transform → router. RequestContext wraps everything for access
    # logging; Transform is innermost so it wraps handler responses. RateLimit
    # sits after JWT/Tenant so per-user buckets can key on the resolved identity.
    app.add_middleware(TransformMiddleware)
    app.add_middleware(RolesMiddleware)
    app.add_middleware(RateLimitMiddleware, settings=settings, limiter=app.state.rate_limiter)
    app.add_middleware(TenantMiddleware)
    app.add_middleware(JWTMiddleware, settings=settings)
    app.add_middleware(CORSMiddleware, allowed_origins=settings.cors_allowed_origins)
    app.add_middleware(HelmetMiddleware)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    register_audit_log_handler()
    register_run_log_handler()
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(tools_router)
    app.include_router(agents_router)
    app.include_router(knowledge_router)
    app.include_router(files_router)
    app.include_router(tasks_router)
    app.include_router(conversations_router)
    app.include_router(run_logs_router)
    app.include_router(dashboard_router)
    app.include_router(models_router)

    register_db_pool_metrics()
    register_rq_metrics(settings.redis_url)

    instrumentator = Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        should_respect_env_var=False,
        excluded_handlers=["/metrics", "/api/health/.*", "/api/health"],
    )
    instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    return app


app = create_app()
