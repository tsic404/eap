"""Security middleware: Helmet, CORS whitelist, rate limit, and identity context.

Pipeline order (outermost → innermost) matches the architecture doc §4.2:

    Helmet → CORS → RateLimit → JWT → Tenant → Roles

Enforcement (401/403 on protected routes) is out of scope here and lands in
TSI-2851 (OIDC/JWT) and TSI-2852 (RBAC). These middlewares establish the
pipeline and resolve request *context* — the identity they discover is bound to
the structlog context and exposed on ``request.state`` for downstream use.
"""

import time
from collections.abc import Iterable
from typing import Any, cast

import jwt
import structlog
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings
from app.errors import error_response

log = structlog.get_logger(__name__)

_HEALTH_PREFIX = "/api/health"


def _set_identity(
    scope: Scope,
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
    role: str | None = None,
) -> None:
    """Stash identity context on the request scope and structlog contextvars."""
    state = scope.setdefault("state", {})
    if user_id is not None:
        state["user_id"] = user_id
        structlog.contextvars.bind_contextvars(user_id=user_id)
    if tenant_id is not None:
        state["tenant_id"] = tenant_id
        structlog.contextvars.bind_contextvars(tenant_id=tenant_id)
    if role is not None:
        state["role"] = role
        structlog.contextvars.bind_contextvars(role=role)

def _header(scope: Scope, name: str) -> str | None:
    headers = cast(list[tuple[bytes, bytes]], scope.get("headers", []))
    for key, value in headers:
        if key.lower() == name.lower().encode():
            return value.decode("latin-1")
    return None


class HelmetMiddleware:
    """Append defensive HTTP headers to every response."""

    _HEADERS: tuple[tuple[bytes, bytes], ...] = (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"x-xss-protection", b"0"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {key.lower() for key, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in self._HEADERS if k not in existing]
                if extra:
                    message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        await self.app(scope, receive, send_wrapper)


class CORSMiddleware:
    """CORS whitelist enforcement.

    Browsers send an ``Origin`` header on cross-origin requests. A missing
    origin (curl, server-to-server) is not a CORS request and passes through.
    A non-whitelisted origin is rejected with 403, satisfying the acceptance
    criterion that the whitelist is enforced server-side rather than relying on
    the browser to block a missing ``Access-Control-Allow-Origin`` header.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        origin = _header(scope, "origin")
        if origin is not None and origin not in self.allowed_origins:
            response = error_response(403, "FORBIDDEN", "Origin not allowed by CORS policy")
            await response(scope, receive, send)
            return

        if scope.get("method") == "OPTIONS" and origin is not None:
            await self._preflight(origin)(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start" and origin is not None:
                headers = list(message.get("headers", []))
                headers.append((b"access-control-allow-origin", origin.encode("latin-1")))
                headers.append((b"vary", b"Origin"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    def _preflight(origin: str) -> Response:
        return Response(
            status_code=204,
            headers={
                "access-control-allow-origin": origin,
                "access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                "access-control-allow-headers": "authorization, content-type, x-request-id",
                "access-control-max-age": "600",
                "vary": "Origin",
            },
        )


class RateLimitMiddleware:
    """Per-IP sliding-window rate limiter.

    In-memory and therefore per-process — acceptable for the single-worker
    skeleton. The Redis-backed token bucket (with per-route limits) is the
    explicit scope of TSI-2867.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.enabled = settings.rate_limit_enabled
        self.limit = settings.rate_limit_requests
        self.window = settings.rate_limit_window_seconds
        self.trusted_proxy_count = settings.trusted_proxy_count
        self._hits: dict[str, list[float]] = {}
        self._last_sweep = 0.0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        if path.startswith(_HEALTH_PREFIX):
            await self.app(scope, receive, send)
            return

        client_ip = self._client_ip(scope)
        now = time.monotonic()
        self._sweep(now)
        cutoff = now - self.window
        hits = [t for t in self._hits.get(client_ip, ()) if t > cutoff]
        if len(hits) >= self.limit:
            self._hits[client_ip] = hits
            response = error_response(429, "RATE_LIMITED", "Rate limit exceeded")
            await response(scope, receive, send)
            return

        hits.append(now)
        self._hits[client_ip] = hits
        await self.app(scope, receive, send)

    def _sweep(self, now: float) -> None:
        # Bound memory by periodically dropping clients whose window has fully
        # expired. Without this, the dict grows without limit across unique IPs.
        if now - self._last_sweep < self.window:
            return
        self._last_sweep = now
        cutoff = now - self.window
        for ip, hits in list(self._hits.items()):
            if not any(t > cutoff for t in hits):
                del self._hits[ip]

    def _client_ip(self, scope: Scope) -> str:
        # Only honour X-Forwarded-For when a known number of trusted proxies sit
        # in front: the rightmost `trusted_proxy_count` entries are appended by
        # those proxies, so the real client is the entry just before them.
        if self.trusted_proxy_count > 0:
            forwarded = _header(scope, "x-forwarded-for")
            if forwarded:
                hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
                if hops:
                    index = max(0, len(hops) - self.trusted_proxy_count)
                    return hops[index]
        client = scope.get("client")
        return str(client[0]) if client else "unknown"


class JWTMiddleware:
    """Decode a bearer token into identity context (no enforcement).

    The signature is always verified (RS256). Without ``jwt_public_key`` no
    token can be authenticated, so identity stays unset rather than trusting an
    unverified payload. Authorization decisions are the ``get_current_user`` /
    ``require_roles`` dependencies of TSI-2851/2852.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.public_key = settings.jwt_public_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        authorization = _header(scope, "authorization")
        if authorization and authorization.lower().startswith("bearer "):
            claims = self._decode(authorization[7:].strip())
            if claims:
                _set_identity(
                    scope,
                    user_id=claims.get("sub"),
                    tenant_id=claims.get("tenantId") or claims.get("tenant_id"),
                    role=claims.get("role"),
                )

        await self.app(scope, receive, send)

    def _decode(self, token: str) -> dict[str, Any] | None:
        if not self.public_key:
            return None
        try:
            return jwt.decode(token, self.public_key, algorithms=["RS256"])
        except jwt.PyJWTError:
            return None


class TenantMiddleware:
    """Resolve tenant id from identity context or the ``X-Tenant-Id`` header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        if state.get("tenant_id") is None:
            tenant_id = _header(scope, "x-tenant-id")
            if tenant_id:
                _set_identity(scope, tenant_id=tenant_id)

        await self.app(scope, receive, send)


class RolesMiddleware:
    """Normalise roles from identity context (defaulting to an empty list)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        if state.get("role") is None:
            _set_identity(scope, role="")

        await self.app(scope, receive, send)
