"""Request context middleware: request-id propagation and access logging."""

import time
import uuid
from typing import cast

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-Id"

log = structlog.get_logger(__name__)


class RequestContextMiddleware:
    """Outermost middleware.

    Assigns a correlation id (forwarding an inbound ``X-Request-Id`` when
    present), seeds the structlog context with ``request_id`` / ``tenant_id`` /
    ``user_id`` (identity middlewares overwrite the latter two), and emits one
    access-log line per request.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._extract_request_id(scope) or str(uuid.uuid4())
        method = str(scope.get("method", ""))
        path = str(scope.get("path", ""))

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=method,
            path=path,
            tenant_id=None,
            user_id=None,
        )

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message["headers"] = self._with_request_id(message, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            log.info(
                "request_completed",
                status_code=status_code,
                duration_ms=round(duration_ms, 2),
            )
            structlog.contextvars.clear_contextvars()

    @staticmethod
    def _extract_request_id(scope: Scope) -> str | None:
        headers = cast(list[tuple[bytes, bytes]], scope.get("headers", []))
        for key, value in headers:
            if key.lower() == REQUEST_ID_HEADER.lower().encode():
                return value.decode("latin-1")
        return None

    @staticmethod
    def _with_request_id(message: Message, request_id: str) -> list[tuple[bytes, bytes]]:
        headers = list(message.get("headers", []))
        present = any(key.lower() == REQUEST_ID_HEADER.lower().encode() for key, _ in headers)
        if not present:
            headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
        return headers
