"""EAP backend application entrypoint: app factory, lifespan, middleware pipeline."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.errors import register_exception_handlers
from app.health import router as health_router
from app.logging_conf import configure_logging, get_logger
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

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    log.info("startup", app_env=settings.app_env, version=settings.app_version)
    try:
        yield
    finally:
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
    # add_middleware prepends, so the LAST added here is the OUTERMOST stage.
    # Execution order: RequestContext → Helmet → CORS → RateLimit → JWT →
    # Tenant → Roles → Transform → router. RequestContext wraps everything for
    # access logging; Transform is innermost so it wraps handler responses.
    app.add_middleware(TransformMiddleware)
    app.add_middleware(RolesMiddleware)
    app.add_middleware(TenantMiddleware)
    app.add_middleware(JWTMiddleware, settings=settings)
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(CORSMiddleware, allowed_origins=settings.cors_allowed_origins)
    app.add_middleware(HelmetMiddleware)
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(health_router)

    return app


app = create_app()
