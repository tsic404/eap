"""TransformMiddleware: wrap successful JSON responses as ``{"data": …}``."""

import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Health probes are the explicit raw-response exception (load balancers and
# orchestrators consume them directly). Their shape is reflected in the shared
# API contract package.
_HEALTH_PREFIX = "/api/health"
_NO_BODY_STATUSES = {204, 304}


class TransformMiddleware:
    """Wrap 2xx JSON responses in the platform data envelope.

    Error responses (4xx/5xx, produced by the global exception handler) already
    carry the ``{"error": …}`` envelope and pass through untouched. Streaming
    responses (``more_body=True``) switch to direct pass-through the moment the
    first chunk arrives, so they are never fully buffered.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._is_health_path(str(scope.get("path", ""))):
            await self.app(scope, receive, send)
            return

        buffered: list[Message] = []
        passthrough = False

        async def capture(message: Message) -> None:
            nonlocal passthrough
            if passthrough:
                await send(message)
                return
            if message["type"] == "http.response.body" and message.get("more_body", False):
                # Streaming response: forward the buffered prefix, then continue
                # passing messages straight through without buffering the rest.
                passthrough = True
                for buffered_message in buffered:
                    await send(buffered_message)
                await send(message)
                return
            buffered.append(message)

        await self.app(scope, receive, capture)

        if passthrough:
            return

        start = next((m for m in buffered if m["type"] == "http.response.start"), None)
        if start is None:
            return

        body_messages = [m for m in buffered if m["type"] == "http.response.body"]
        if not self._should_wrap(start["status"], start.get("headers", [])):
            for message in buffered:
                await send(message)
            return

        raw_body = b"".join(m.get("body", b"") for m in body_messages)
        wrapped_body = self._wrap(raw_body)
        start["headers"] = self._replace_content_length(start.get("headers", []), wrapped_body)
        await send(start)
        await send({"type": "http.response.body", "body": wrapped_body})

    @staticmethod
    def _is_health_path(path: str) -> bool:
        return path == _HEALTH_PREFIX or path.startswith(_HEALTH_PREFIX + "/")

    @staticmethod
    def _should_wrap(status_code: int, headers: list[tuple[bytes, bytes]]) -> bool:
        if status_code not in range(200, 300) or status_code in _NO_BODY_STATUSES:
            return False
        content_type = b""
        for key, value in headers:
            if key.lower() == b"content-type":
                content_type = value
                break
        return content_type.startswith(b"application/json")

    @staticmethod
    def _wrap(raw_body: bytes) -> bytes:
        try:
            payload: Any = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError:
            payload = None
        return json.dumps({"data": payload}, ensure_ascii=False, separators=(",", ":")).encode()

    @staticmethod
    def _replace_content_length(
        headers: list[tuple[bytes, bytes]], body: bytes
    ) -> list[tuple[bytes, bytes]]:
        content_length = str(len(body)).encode()
        replaced = False
        result: list[tuple[bytes, bytes]] = []
        for key, value in headers:
            if key.lower() == b"content-length":
                result.append((key, content_length))
                replaced = True
            else:
                result.append((key, value))
        if not replaced:
            result.append((b"content-length", content_length))
        return result
