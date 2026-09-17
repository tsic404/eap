"""ToolProxy: external enterprise-API proxy (HTTP + retry + circuit breaker + masking).

Wraps a registered tool's HTTP endpoint with retry on transient failures,
exponential backoff, per-tool circuit breaker, credential injection, and PII
masking. Request bodies are sent verbatim (the upstream needs real values);
masking applies to the response and to the caller's log/audit copies only.
See architecture doc §32.5.3.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.errors import AppError
from app.models.tool import ToolRegistry

logger = structlog.get_logger(__name__)

# Upstream statuses that signal a transient failure worth retrying.
_RETRYABLE_STATUS_CODES = frozenset({502, 503, 504})

# Field-name substrings whose values are masked (case-insensitive). Covers the
# common PII/credential fields named in the scope: 身份证/手机号/银行卡号/密码/
# 邮箱/邮件, plus specific name keys.
_PII_KEY_SUBSTRINGS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "id_card",
    "idcard",
    "phone",
    "mobile",
    "email",
    "mail",
    "first_name",
    "last_name",
    "full_name",
    "display_name",
    "user_name",
    "contact_name",
    "real_name",
    "bank_card",
    "bankcard",
    "card_no",
    "cardno",
    "ssn",
    "身份证",
    "手机",
    "银行卡",
    "密码",
)

# Keys masked only on exact match — bare "name" as a substring would also hit
# filename/hostname/table_name operational metadata.
_PII_EXACT_KEYS = frozenset({"name"})


@dataclass
class CircuitBreakerState:
    """Failure accounting for a single tool."""

    failures: int = 0
    opened_at: float = 0.0
    open: bool = False
    half_open: bool = False


@dataclass
class ExecutionResult:
    """Outcome of a single tool HTTP call."""

    status_code: int
    body: Any
    latency_ms: int
    attempts: int


# Free-text PII patterns (masked inside string values/response bodies).
_PII_TEXT_PATTERNS = (
    re.compile(r"1[3-9]\d{9}"),  # CN mobile (11 digits)
    re.compile(r"\d{17}[\dXx]"),  # CN id card (18 digits)
    re.compile(r"\d{16,19}"),  # bank/card number
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),  # email
)


def mask_pii(data: Any) -> Any:
    """Recursively replace PII values with ``"***"``.

    Structured data is masked by key name; free-text strings are masked by
    pattern (mobile / id-card / bank-card / email).
    """
    if isinstance(data, dict):
        return {
            key: "***" if _is_pii_key(str(key)) else mask_pii(value)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [mask_pii(item) for item in data]
    if isinstance(data, str):
        masked = data
        for pattern in _PII_TEXT_PATTERNS:
            masked = pattern.sub("***", masked)
        return masked
    return data


def _is_pii_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in _PII_EXACT_KEYS or any(
        sub in normalized for sub in _PII_KEY_SUBSTRINGS
    )


AuthInjector = Callable[[ToolRegistry], dict[str, str]]
Masker = Callable[[Any], Any]


def inject_auth(tool: ToolRegistry) -> dict[str, str]:
    """Build request headers from ``auth_type`` + ``auth_config``."""
    config = tool.auth_config or {}
    auth_type = tool.auth_type or "none"
    if auth_type == "bearer":
        token = config.get("token", "")
        return {"Authorization": f"Bearer {token}"} if token else {}
    if auth_type == "api_key":
        header_name = str(config.get("header_name") or "X-API-Key")
        key = config.get("api_key") or config.get("key", "")
        return {header_name: str(key)} if key else {}
    if auth_type == "basic":
        username = str(config.get("username", ""))
        password = str(config.get("password", ""))
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    if auth_type == "oauth2":
        token = config.get("access_token", "")
        return {"Authorization": f"Bearer {token}"} if token else {}
    return {}


class ToolProxy:
    """Async tool HTTP proxy: retry + per-tool circuit breaker + masking."""

    CIRCUIT_FAILURE_THRESHOLD = 5
    CIRCUIT_RECOVERY_SECONDS = 60.0

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        inject_auth_fn: AuthInjector = inject_auth,
        mask_fn: Masker = mask_pii,
    ) -> None:
        # ``transport`` is a testing seam: production uses httpx's default pool,
        # tests inject a MockTransport so no external API is contacted.
        self._client = httpx.AsyncClient(transport=transport)
        self._inject_auth = inject_auth_fn
        self._mask = mask_fn
        self._circuit_breakers: dict[str, CircuitBreakerState] = {}

    async def execute(self, tool: ToolRegistry, params: dict[str, Any]) -> ExecutionResult:
        """Run a tool with breaker + retry; raise ``AppError`` on failure.

        The request body is sent verbatim so the upstream sees real values;
        only the response and the caller's audit copies are masked.
        """
        self._raise_if_circuit_open(tool.tool_id)
        headers = self._inject_auth(tool)
        max_retries = self._max_retries(tool)
        start = time.monotonic()
        last_error: Exception | None = None
        attempts = 0
        for attempt in range(max_retries + 1):
            attempts += 1
            try:
                response = await self._request(tool, params, headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
            else:
                if 200 <= response.status_code < 300:
                    self._record_success(tool.tool_id)
                    return ExecutionResult(
                        status_code=response.status_code,
                        body=self._mask(self._parse_body(response)),
                        latency_ms=int((time.monotonic() - start) * 1000),
                        attempts=attempts,
                    )
                last_error = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
                if response.status_code not in _RETRYABLE_STATUS_CODES:
                    break
            if attempt == max_retries:
                break
            await asyncio.sleep(self._backoff_ms(tool, attempt) / 1000)

        self._record_failure(tool.tool_id)
        raise AppError(502, "TOOL_UPSTREAM_ERROR", f"Tool request failed: {last_error}")

    async def debug(self, tool: ToolRegistry, params: dict[str, Any]) -> ExecutionResult:
        """Run a single-shot test call (no retry, no breaker) for the debug panel."""
        headers = self._inject_auth(tool)
        start = time.monotonic()
        try:
            response = await self._request(tool, params, headers)
            status_code = response.status_code
            body = self._mask(self._parse_body(response))
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise AppError(502, "TOOL_UPSTREAM_ERROR", f"Tool request failed: {exc}") from exc
        return ExecutionResult(
            status_code=status_code,
            body=body,
            latency_ms=int((time.monotonic() - start) * 1000),
            attempts=1,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client (call on application shutdown)."""
        await self._client.aclose()

    async def _request(
        self,
        tool: ToolRegistry,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        if not tool.endpoint:
            raise AppError(422, "TOOL_NOT_CONFIGURED", "Tool has no endpoint")
        request_headers = {"Content-Type": "application/json", **headers}
        method = (tool.method or "POST").upper()
        kwargs: dict[str, Any] = {
            "method": method,
            "url": tool.endpoint,
            "headers": request_headers,
            "timeout": tool.timeout_ms / 1000,
        }
        if method == "GET":
            kwargs["params"] = params
        else:
            kwargs["json"] = params
        return await self._client.request(**kwargs)

    @staticmethod
    def _parse_body(response: httpx.Response) -> Any:
        if response.status_code == 204:
            return None
        if "application/json" in response.headers.get("content-type", ""):
            try:
                return response.json()
            except json.JSONDecodeError:
                return response.text
        return response.text

    def _max_retries(self, tool: ToolRegistry) -> int:
        policy: dict[str, Any] = tool.retry_policy or {}
        try:
            return max(0, int(policy.get("max_retries", 2)))
        except (TypeError, ValueError):
            return 2

    def _backoff_ms(self, tool: ToolRegistry, attempt: int) -> int:
        policy: dict[str, Any] = tool.retry_policy or {}
        try:
            base = max(0, int(policy.get("base_delay_ms", 1000)))
        except (TypeError, ValueError):
            base = 1000
        # ``base << attempt`` == ``base * 2**attempt``; the shift keeps mypy's
        # inferred type ``int`` (``int.__pow__`` is typed ``Any``).
        return base << attempt

    def _raise_if_circuit_open(self, tool_id: str) -> None:
        breaker = self._circuit_breakers.get(tool_id)
        if breaker is None or not breaker.open:
            return
        if time.monotonic() - breaker.opened_at < self.CIRCUIT_RECOVERY_SECONDS:
            raise AppError(503, "TOOL_CIRCUIT_OPEN", "Circuit breaker open for tool")
        # Recovery window elapsed → half-open. ``open`` stays True so concurrent
        # callers are still rejected; the trial request alone is admitted.
        if breaker.half_open:
            raise AppError(503, "TOOL_CIRCUIT_OPEN", "Circuit breaker open for tool")
        breaker.half_open = True

    def _record_failure(self, tool_id: str) -> None:
        breaker = self._circuit_breakers.setdefault(tool_id, CircuitBreakerState())
        breaker.failures += 1
        breaker.half_open = False
        if breaker.failures >= self.CIRCUIT_FAILURE_THRESHOLD:
            breaker.open = True
            breaker.opened_at = time.monotonic()
            logger.warning(
                "tool_circuit_breaker_opened",
                tool_id=tool_id,
                failures=breaker.failures,
            )

    def _record_success(self, tool_id: str) -> None:
        self._circuit_breakers.pop(tool_id, None)
