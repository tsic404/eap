"""TC-ops-07 — the readiness probe's engine entry fails closed without a usable provider.

Scenario semantics: ``docs/03-acceptance.md`` (TC-ops-07 → AC-38, P0) over
``docs/02-contracts.md`` C-17 / C-18. Every constructed no-usable-provider
state answers 503 with status degraded, the engine entry flagged and the other
two checks untouched; a judgement that cannot be completed — rejected console
credentials or an unanswered provider query — fails the same way within the
seconds-level bound instead of hanging.

Every scenario payload, the console double and the dependency stubs are the
constants and helpers ``tests/test_health.py`` already drives; no part of the
readiness judgement is repeated here.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from app import health as health_module
from app.dify_console import DifyConsoleError
from tests.test_health import (
    _CREDENTIALLED_PROVIDERS,
    _EXHAUSTED_QUOTA_PROVIDERS,
    _INSTALLED_BUT_UNCONFIGURED_PROVIDERS,
    _ConsoleStub,
    _ready_client,
    _stub_dependency_pings,
)

TC_ID = "TC-ops-07"

# C-17: the raw response is exactly status plus the three dependency checks —
# no `data` envelope.
_DEGRADED_BODY = {
    "status": "degraded",
    "checks": {"database": "ok", "redis": "ok", "dify": "error"},
}

_FAST_PROVIDER_TIMEOUT_SECONDS = 0.05
_SLOW_PROVIDER_REPLY_SECONDS = 5.0
# C-18 caps the provider judgement at 5 seconds.
_CONTRACT_PROVIDER_CAP_SECONDS = 5.0
# A reply that outlives the stubbed bound must not be waited on. Generous
# against scheduling noise, far below the 5s reply.
_RESPONSE_CEILING_SECONDS = 1.0


def _assert_degraded(resp: httpx.Response) -> None:
    assert resp.status_code == 503
    assert resp.json() == _DEGRADED_BODY


def test_shipped_provider_timeout_stays_within_the_contract_bound() -> None:
    # The stubbed case below proves a slow reply does not block; this pins the
    # shipped bound itself, which no stub can weaken.
    assert health_module._PROVIDER_TIMEOUT_SECONDS <= _CONTRACT_PROVIDER_CAP_SECONDS


def test_no_provider_degrades_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dependency_pings(monkeypatch)

    resp = _ready_client(_ConsoleStub(payload={"data": []})).get("/api/health/ready")

    _assert_degraded(resp)


def test_installed_but_unconfigured_provider_degrades_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dependency_pings(monkeypatch)

    resp = _ready_client(_ConsoleStub(payload=_INSTALLED_BUT_UNCONFIGURED_PROVIDERS)).get(
        "/api/health/ready"
    )

    _assert_degraded(resp)


def test_exhausted_managed_quota_degrades_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dependency_pings(monkeypatch)

    resp = _ready_client(_ConsoleStub(payload=_EXHAUSTED_QUOTA_PROVIDERS)).get("/api/health/ready")

    _assert_degraded(resp)


def test_rejected_console_query_degrades_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dependency_pings(monkeypatch)
    console = _ConsoleStub(error=DifyConsoleError(401, "unauthorized"))

    resp = _ready_client(console).get("/api/health/ready")

    _assert_degraded(resp)


def test_provider_judgement_timeout_answers_within_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dependency_pings(monkeypatch)
    monkeypatch.setattr(health_module, "_PROVIDER_TIMEOUT_SECONDS", _FAST_PROVIDER_TIMEOUT_SECONDS)
    console = _ConsoleStub(payload=_CREDENTIALLED_PROVIDERS)

    async def _never_answers() -> Any:
        await asyncio.sleep(_SLOW_PROVIDER_REPLY_SECONDS)
        return _CREDENTIALLED_PROVIDERS

    monkeypatch.setattr(console, "get_model_providers", _never_answers)
    client = _ready_client(console)
    started = time.monotonic()
    resp = client.get("/api/health/ready")
    elapsed = time.monotonic() - started

    _assert_degraded(resp)
    assert elapsed < _RESPONSE_CEILING_SECONDS
