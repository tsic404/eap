"""Health endpoint tests."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import health as health_module
from app.config import Settings
from app.dify_console import DifyConsoleError
from app.main import create_app


def test_health_live_returns_raw_ok(client: TestClient) -> None:
    resp = client.get("/api/health/live")
    assert resp.status_code == 200
    # Health probes are the raw-response exception — no data envelope.
    assert resp.json() == {"status": "ok"}


def test_health_info_returns_raw_service_info(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "eap-backend"
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"


def test_health_ready_reports_dependency_status(client: TestClient) -> None:
    resp = client.get("/api/health/ready")
    body = resp.json()
    # Without Postgres/Redis/Dify the probe reports degraded but still returns
    # the structured per-dependency status.
    assert set(body["checks"]) == {"database", "redis", "dify"}
    assert body["status"] in {"ok", "degraded"}
    for value in body["checks"].values():
        assert value in {"ok", "error"}
    assert resp.status_code == (200 if body["status"] == "ok" else 503)


# Provider payloads recorded from langgenius/dify-api:1.17.0's
# GET /console/api/workspaces/current/model-providers.
_CREDENTIALLED_PROVIDERS = {
    "data": [
        {
            "provider": "langgenius/openai_api_compatible/openai_api_compatible",
            "preferred_provider_type": "custom",
            "custom_configuration": {
                "status": "active",
                "custom_models": [{"model": "bge-m3", "model_type": "text-embedding"}],
            },
            "system_configuration": {"enabled": False},
        }
    ]
}
_INSTALLED_BUT_UNCONFIGURED_PROVIDERS = {
    "data": [
        {
            "provider": "openai",
            "preferred_provider_type": "system",
            "custom_configuration": {"status": "no_configure", "custom_models": []},
            "system_configuration": {"enabled": False},
        }
    ]
}


class _ConsoleStub:
    """DifyConsoleClient double: one canned reply plus the probe call count."""

    def __init__(self, payload: Any = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls = 0

    async def get_model_providers(self) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.payload


def _ready_client(console: object) -> TestClient:
    """A TestClient whose Dify console answers from a stub, not the network."""
    app: FastAPI = create_app(Settings(_env_file=None, rate_limit_enabled=False))
    app.state.dify_console = console
    return TestClient(app, raise_server_exceptions=False)


def _stub_dependency_pings(monkeypatch: pytest.MonkeyPatch, *, dify_reachable: bool = True) -> None:
    """Answer the raw pings offline so only the provider probe decides."""

    async def _ping(*_args: object, **_kwargs: object) -> bool:
        return True

    async def _ping_dify(*_args: object, **_kwargs: object) -> bool:
        return dify_reachable

    monkeypatch.setattr(health_module, "_ping_database", _ping)
    monkeypatch.setattr(health_module, "_ping_redis", _ping)
    monkeypatch.setattr(health_module, "_ping_dify", _ping_dify)


def test_health_ready_accepts_workspace_with_credentialled_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dependency_pings(monkeypatch)
    console = _ConsoleStub(payload=_CREDENTIALLED_PROVIDERS)

    resp = _ready_client(console).get("/api/health/ready")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok", "dify": "ok"},
    }
    assert console.calls == 1


def test_health_ready_accepts_enabled_hosted_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dependency_pings(monkeypatch)
    payload = {
        "data": [
            {
                "provider": "openai",
                "custom_configuration": {"status": "no_configure", "custom_models": []},
                "system_configuration": {"enabled": True},
            }
        ]
    }

    resp = _ready_client(_ConsoleStub(payload=payload)).get("/api/health/ready")

    assert resp.status_code == 200
    assert resp.json()["checks"]["dify"] == "ok"


def test_health_ready_rejects_workspace_without_any_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dependency_pings(monkeypatch)

    resp = _ready_client(_ConsoleStub(payload={"data": []})).get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json() == {
        "status": "degraded",
        "checks": {"database": "ok", "redis": "ok", "dify": "error"},
    }


def test_health_ready_rejects_installed_but_unconfigured_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dependency_pings(monkeypatch)
    console = _ConsoleStub(payload=_INSTALLED_BUT_UNCONFIGURED_PROVIDERS)

    resp = _ready_client(console).get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json()["checks"]["dify"] == "error"


def test_health_ready_rejects_provider_query_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dependency_pings(monkeypatch)
    console = _ConsoleStub(error=DifyConsoleError(401, "unauthorized"))

    resp = _ready_client(console).get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json()["checks"]["dify"] == "error"


def test_health_ready_rejects_provider_query_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dependency_pings(monkeypatch)
    monkeypatch.setattr(health_module, "_PROVIDER_TIMEOUT_SECONDS", 0.05)

    class _SlowConsole:
        async def get_model_providers(self) -> Any:
            await asyncio.sleep(5)
            return _CREDENTIALLED_PROVIDERS

    resp = _ready_client(_SlowConsole()).get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json()["checks"]["dify"] == "error"


def test_health_ready_skips_provider_probe_when_dify_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dependency_pings(monkeypatch, dify_reachable=False)
    console = _ConsoleStub(payload=_CREDENTIALLED_PROVIDERS)

    resp = _ready_client(console).get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json()["checks"]["dify"] == "error"
    # A doomed console login per probe is pointless when Dify is already down.
    assert console.calls == 0
