"""DifyConsoleClient tests (Session login, cookie/CSRF, retry, health, endpoints)."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

import httpx
import pytest

from app.config import Settings
from app.dify_console import (
    CreateAppParams,
    CreateDatasetParams,
    DifyConsoleClient,
    DifyConsoleError,
    ModelConfig,
    UpdateAppParams,
)

_EMAIL = "admin@example.com"
_PASSWORD = "s3cret"
_BASE_URL = "http://dify.test"

_ACCESS_TOKEN = "access-token-value"
_CSRF_TOKEN = "csrf-token-value"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        dify_api_base_url=_BASE_URL,
        dify_console_email=_EMAIL,
        dify_console_password=_PASSWORD,
    )


def _login_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"result": "success"},
        headers=[
            ("Set-Cookie", f"access_token={_ACCESS_TOKEN}; Path=/"),
            ("Set-Cookie", "refresh_token=refresh-token-value; Path=/"),
            ("Set-Cookie", f"csrf_token={_CSRF_TOKEN}; Path=/"),
        ],
    )


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[DifyConsoleClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DifyConsoleClient(_settings(), client=http), http


def _json_body(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content)


@pytest.mark.asyncio
async def test_login_base64_encodes_password() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = _json_body(request)
        return _login_response()

    client, _ = _make_client(handler)
    await client.login()

    assert captured["path"] == "/console/api/login"
    assert captured["body"]["email"] == _EMAIL
    # Dify console login Base64-encodes the password (decoded server-side).
    assert captured["body"]["password"] == base64.b64encode(_PASSWORD.encode("utf-8")).decode(
        "ascii"
    )


@pytest.mark.asyncio
async def test_request_carries_session_cookie_and_csrf_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return _login_response()
        seen["cookie"] = request.headers.get("cookie", "")
        seen["csrf"] = request.headers.get("x-csrf-token", "")
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json={"data": []})

    client, _ = _make_client(handler)
    await client.health_check()

    assert "access_token=access-token-value" in seen["cookie"]
    assert "csrf_token=csrf-token-value" in seen["cookie"]
    assert seen["csrf"] == _CSRF_TOKEN
    assert seen["query"] == "limit=1"


@pytest.mark.asyncio
async def test_request_relogs_in_and_retries_on_401() -> None:
    login_calls = 0
    apps_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_calls, apps_calls
        if request.url.path == "/console/api/login":
            login_calls += 1
            return _login_response()
        apps_calls += 1
        if apps_calls == 1:
            return httpx.Response(401, json={"message": "unauthorized"})
        return httpx.Response(200, json={"id": "app-123"})

    client, _ = _make_client(handler)
    await client.login()

    result = await client.create_app(CreateAppParams(name="x", mode="chat"))

    assert result == {"id": "app-123"}
    assert login_calls == 2  # initial login + re-login after 401
    assert apps_calls == 2  # first attempt (401) + retry (200)


@pytest.mark.asyncio
async def test_health_check_reports_disconnected_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, _ = _make_client(handler)
    assert await client.health_check() == {"status": "disconnected"}


@pytest.mark.asyncio
async def test_health_check_reports_disconnected_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return _login_response()
        return httpx.Response(500, json={"message": "boom"})

    client, _ = _make_client(handler)
    assert await client.health_check() == {"status": "disconnected"}


@pytest.mark.asyncio
async def test_create_app_returns_app_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return _login_response()
        assert request.url.path == "/console/api/apps"
        return httpx.Response(200, json={"id": "app-123", "name": "x", "mode": "chat"})

    client, _ = _make_client(handler)
    app = await client.create_app(CreateAppParams(name="x", mode="chat", description="desc"))

    assert app["id"] == "app-123"


@pytest.mark.asyncio
async def test_configure_model_sends_provider_and_prompt() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return _login_response()
        captured["path"] = request.url.path
        captured["body"] = _json_body(request)
        return httpx.Response(200, json={"result": "success"})

    client, _ = _make_client(handler)
    await client.configure_model(
        "app-123",
        ModelConfig(provider="openai", model="gpt-4o", configs={"prompt_template": "hello"}),
    )

    assert captured["path"] == "/console/api/apps/app-123/model-config"
    assert captured["body"]["provider"] == "openai"
    assert captured["body"]["model"] == "gpt-4o"
    assert captured["body"]["configs"] == {"prompt_template": "hello"}


@pytest.mark.asyncio
async def test_create_api_key_hits_apps_api_keys_endpoint() -> None:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return _login_response()
        captured.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"id": "key-1", "token": "app-secret"})

    client, _ = _make_client(handler)
    key = await client.create_api_key("app-123")

    assert key["id"] == "key-1"
    assert captured == ["POST /console/api/apps/app-123/api-keys"]


@pytest.mark.asyncio
async def test_api_error_raises_dify_console_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return _login_response()
        return httpx.Response(422, json={"message": "invalid"})

    client, _ = _make_client(handler)
    with pytest.raises(DifyConsoleError) as exc_info:
        await client.create_dataset(CreateDatasetParams(name="kb"))
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_startup_skips_login_when_unconfigured() -> None:
    settings = Settings(
        _env_file=None,
        dify_api_base_url=_BASE_URL,
        dify_console_email="",
        dify_console_password="",
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DifyConsoleClient(settings, client=http)
    await client.startup()
    await client.shutdown()

    assert calls == []


@pytest.mark.asyncio
async def test_unconfigured_request_raises_without_login() -> None:
    settings = Settings(
        _env_file=None,
        dify_api_base_url=_BASE_URL,
        dify_console_email="",
        dify_console_password="",
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DifyConsoleClient(settings, client=http)
    with pytest.raises(DifyConsoleError) as exc_info:
        await client.create_app(CreateAppParams(name="x", mode="chat"))

    assert exc_info.value.status_code == 0
    assert calls == []  # no request to Dify, not even a login


@pytest.mark.asyncio
async def test_unconfigured_health_check_disconnected() -> None:
    settings = Settings(
        _env_file=None,
        dify_api_base_url=_BASE_URL,
        dify_console_email="",
        dify_console_password="",
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = DifyConsoleClient(settings, client=http)
    assert await client.health_check() == {"status": "disconnected"}
    assert calls == []


@pytest.mark.asyncio
async def test_update_app_sends_typed_params() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return _login_response()
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = _json_body(request)
        return httpx.Response(200, json={"id": "app-123"})

    client, _ = _make_client(handler)
    await client.update_app("app-123", UpdateAppParams(name="renamed", description="desc"))

    assert captured["method"] == "PUT"
    assert captured["path"] == "/console/api/apps/app-123"
    assert captured["body"] == {"name": "renamed", "description": "desc"}


@pytest.mark.asyncio
async def test_get_models_quotes_provider_path() -> None:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/api/login":
            return _login_response()
        captured.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"data": []})

    client, _ = _make_client(handler)
    await client.get_models("langgenius/openai/openai")
    await client.get_models("acme/llm v2")

    # "/" stays unencoded so Dify's `<path:provider>` converter still matches,
    # while the space is percent-encoded to keep it out of the raw path.
    assert captured == [
        "/console/api/workspaces/current/model-providers/langgenius/openai/openai/models",
        "/console/api/workspaces/current/model-providers/acme/llm%20v2/models",
    ]


@pytest.mark.asyncio
async def test_refresh_session_forces_relogin() -> None:
    login_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_calls
        if request.url.path == "/console/api/login":
            login_calls += 1
            return _login_response()
        return httpx.Response(200, json={})

    client, _ = _make_client(handler)
    await client.login()  # initial login
    assert login_calls == 1

    await client.login()  # non-force: session already valid → no-op
    assert login_calls == 1

    await client._refresh_session()  # scheduled refresh must actually re-login
    assert login_calls == 2
