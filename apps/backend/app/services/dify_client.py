"""Async client for the Dify Service API (Bearer-token auth).

Wraps the terminal-user Service API endpoints with retry on transient
502/504 responses, exponential backoff, per-request/stream timeouts, and a
per-path circuit breaker. See architecture doc §21.1.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

import httpx
import structlog

from app.core.exceptions import DifyApiError

logger = structlog.get_logger(__name__)

# Upstream statuses that signal a transient Dify-side failure worth retrying.
_RETRYABLE_STATUS_CODES = frozenset({502, 504})


@dataclass
class _CircuitState:
    """Failure accounting for a single upstream path."""

    failures: int = 0
    opened_at: float = 0.0
    open: bool = False
    # True while a single trial request is in flight after the recovery window
    # elapses; guards against admitting more than one concurrent trial.
    half_open: bool = False


class DifyClientService:
    """Async Dify Service-API client: retry + per-path circuit breaker."""

    CIRCUIT_FAILURE_THRESHOLD = 5
    CIRCUIT_RECOVERY_SECONDS = 60.0
    REQUEST_TIMEOUT_SECONDS = 30.0
    STREAM_TIMEOUT_SECONDS = 120.0

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # ``transport`` is a testing seam: production uses httpx's default pool,
        # tests inject a MockTransport so no live Dify instance is required.
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(self.REQUEST_TIMEOUT_SECONDS),
            transport=transport,
        )
        self._circuit_breakers: dict[str, _CircuitState] = {}

    async def get(
        self,
        path: str,
        query: Mapping[str, str] | None = None,
        *,
        retries: int = 3,
    ) -> dict[str, Any]:
        """GET ``path`` and return the JSON body, retrying transient failures."""

        async def _request() -> httpx.Response:
            return await self._client.get(path, params=dict(query) if query else None)

        return await self._with_retry(path, _request, retries)

    async def post(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        retries: int = 3,
    ) -> dict[str, Any]:
        """POST ``body`` as JSON and return the JSON response, retrying transient failures."""

        async def _request() -> httpx.Response:
            return await self._client.post(
                path,
                json=dict(body),
                headers={"Content-Type": "application/json"},
            )

        return await self._with_retry(path, _request, retries)

    async def post_stream(
        self,
        path: str,
        body: Mapping[str, Any],
    ) -> AsyncIterator[bytes]:
        """POST ``body`` and stream the SSE response as raw bytes.

        Streaming responses cannot be safely replayed, so there is no retry:
        a failed stream records a circuit failure and raises immediately.
        """
        self._raise_if_circuit_open(path)
        try:
            async with self._client.stream(
                "POST",
                path,
                json=dict(body),
                headers={"Content-Type": "application/json"},
                timeout=httpx.Timeout(self.STREAM_TIMEOUT_SECONDS),
            ) as response:
                if response.is_error:
                    body_text = (await response.aread()).decode("utf-8", errors="replace")
                    raise DifyApiError(response.status_code, body_text)
                async for chunk in response.aiter_bytes():
                    yield chunk
                # Record success only after the body has been fully consumed: a
                # timeout/transport error mid-stream is a failure, not a success.
                self._record_success(path)
        except DifyApiError:
            self._record_failure(path)
            raise
        except httpx.TransportError as exc:
            self._record_failure(path)
            raise DifyApiError(504, f"Dify stream interrupted: {exc}") from exc

    async def aclose(self) -> None:
        """Close the underlying HTTP client (call on application shutdown)."""
        await self._client.aclose()

    async def _with_retry(
        self,
        path: str,
        request: Callable[[], Awaitable[httpx.Response]],
        retries: int,
    ) -> dict[str, Any]:
        self._raise_if_circuit_open(path)
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = await request()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # Network/timeout failures are transient; retry and, once
                # exhausted, surface them as a Dify timeout (504).
                last_error = exc
            else:
                if not response.is_error:
                    try:
                        parsed = response.json()
                    except json.JSONDecodeError:
                        self._record_failure(path)
                        raise DifyApiError(502, "Dify returned a non-JSON response") from None
                    self._record_success(path)
                    return cast(dict[str, Any], parsed)
                last_error = DifyApiError(response.status_code, response.text)
                if response.status_code not in _RETRYABLE_STATUS_CODES:
                    break
            if attempt == retries:
                break
            await asyncio.sleep(2**attempt)

        self._record_failure(path)
        if isinstance(last_error, DifyApiError):
            raise last_error
        raise DifyApiError(504, f"Dify API request failed: {last_error}") from last_error

    def _raise_if_circuit_open(self, path: str) -> None:
        breaker = self._circuit_breakers.get(path)
        if breaker is None or not breaker.open:
            return
        if time.monotonic() - breaker.opened_at < self.CIRCUIT_RECOVERY_SECONDS:
            raise DifyApiError(503, "Circuit breaker open for Dify API")
        # Recovery window elapsed → half-open. Admit exactly one trial request:
        # ``open`` stays True and ``half_open`` reserves the trial so concurrent
        # callers are still rejected with 503.
        if breaker.half_open:
            raise DifyApiError(503, "Circuit breaker open for Dify API")
        breaker.half_open = True

    def _record_failure(self, path: str) -> None:
        breaker = self._circuit_breakers.setdefault(path, _CircuitState())
        breaker.failures += 1
        breaker.half_open = False
        if breaker.failures >= self.CIRCUIT_FAILURE_THRESHOLD:
            breaker.open = True
            breaker.opened_at = time.monotonic()
            logger.warning(
                "circuit_breaker.opened",
                path=path,
                failures=breaker.failures,
            )

    def _record_success(self, path: str) -> None:
        self._circuit_breakers.pop(path, None)
