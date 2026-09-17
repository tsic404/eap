"""ToolProxy unit tests (httpx MockTransport — no external API required)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app.errors import AppError
from app.models.tool import ToolRegistry
from app.services.tool_proxy import ToolProxy, inject_auth, mask_pii

Handler = Callable[[httpx.Request], httpx.Response]


def _make_tool(**overrides: object) -> ToolRegistry:
    fields: dict[str, object] = {
        "tool_id": "my-tool",
        "name": "My Tool",
        "type": "http",
        "endpoint": "http://tool.example/api",
        "method": "POST",
        "risk_level": "medium",
        "permission_mode": "auto",
        "auth_type": "none",
        "auth_config": None,
        "timeout_ms": 10000,
        "retry_policy": {"max_retries": 2, "base_delay_ms": 1000},
        "status": "active",
    }
    fields.update(overrides)
    return ToolRegistry(**fields)  # type: ignore[arg-type]


def _proxy(handler: Handler) -> ToolProxy:
    return ToolProxy(transport=httpx.MockTransport(handler))


def _freeze_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise exponential backoff so retry tests run without wall-clock delays."""

    async def _noop_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.services.tool_proxy.asyncio.sleep", _noop_sleep)


# -- masking ---------------------------------------------------------------


def test_mask_pii_masks_sensitive_fields_recursively() -> None:
    data = {
        "user": {
            "phone": "13800138000",
            "id_card": "110101",
            "name": "张三",
            "full_name": "张三",
            "email": "alice@acme.com",
            "mail": "bob@acme.com",
        },
        "items": [{"password": "s3cret"}],
    }
    masked = mask_pii(data)
    assert masked["user"]["phone"] == "***"
    assert masked["user"]["id_card"] == "***"
    assert masked["user"]["name"] == "***"
    assert masked["user"]["full_name"] == "***"
    assert masked["user"]["email"] == "***"
    assert masked["user"]["mail"] == "***"
    assert masked["items"][0]["password"] == "***"


def test_mask_pii_does_not_mask_operational_name_metadata() -> None:
    data = {
        "filename": "report.pdf",
        "hostname": "node-1",
        "table_name": "audit_logs",
        "username": "ops-user",
    }
    assert mask_pii(data) == data


def test_mask_pii_masks_card_number_keys() -> None:
    data = {
        "card_number": "opaque-value",
        "card_num": "opaque-value",
        "card_no": "opaque-value",
        "cardno": "opaque-value",
        "bank_card": "opaque-value",
        "bankcard": "opaque-value",
    }
    masked = mask_pii(data)
    assert masked["card_number"] == "***"
    assert masked["card_num"] == "***"
    assert masked["card_no"] == "***"
    assert masked["cardno"] == "***"
    assert masked["bank_card"] == "***"
    assert masked["bankcard"] == "***"


def test_mask_pii_does_not_mask_operational_card_metadata() -> None:
    data = {
        "table_card_count": 1024,
        "cardinality": 512,
        "order_card_no": "ORD-1001",
    }
    assert mask_pii(data) == data


def test_mask_pii_masks_free_text_mobile() -> None:
    assert mask_pii("call 13800138000 now") == "call *** now"


def test_mask_pii_masks_free_text_email() -> None:
    assert mask_pii("contact alice@acme.com for help") == "contact *** for help"


# -- auth injection ----------------------------------------------------------


def test_inject_auth_none() -> None:
    assert inject_auth(_make_tool()) == {}


def test_inject_auth_bearer() -> None:
    tool = _make_tool(auth_type="bearer", auth_config={"token": "tok123"})
    assert inject_auth(tool) == {"Authorization": "Bearer tok123"}


def test_inject_auth_api_key() -> None:
    tool = _make_tool(
        auth_type="api_key",
        auth_config={"header_name": "X-Key", "api_key": "secret"},
    )
    assert inject_auth(tool) == {"X-Key": "secret"}


def test_inject_auth_basic() -> None:
    tool = _make_tool(auth_type="basic", auth_config={"username": "u", "password": "p"})
    assert inject_auth(tool) == {"Authorization": "Basic dTpw"}


# -- execute ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_returns_masked_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"phone": "13800138000", "ok": "yes"})

    result = await _proxy(handler).execute(_make_tool(), {})
    assert result.status_code == 200
    assert result.body == {"phone": "***", "ok": "yes"}


@pytest.mark.asyncio
async def test_execute_sends_params_verbatim() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    await _proxy(handler).execute(_make_tool(), {"phone": "13800138000", "name": "张三"})
    # The upstream must receive real values — masking the request would corrupt
    # business queries. Masking applies to the response and audit copies only.
    assert captured["body"] == {"phone": "13800138000", "name": "张三"}


@pytest.mark.asyncio
async def test_execute_masks_text_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="call 13800138000 now", headers={"content-type": "text/plain"}
        )

    result = await _proxy(handler).execute(_make_tool(), {})
    assert result.body == "call *** now"


@pytest.mark.asyncio
async def test_execute_retries_on_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"error": "retry"})
        return httpx.Response(200, json={"ok": True})

    result = await _proxy(handler).execute(_make_tool(), {"a": 1})
    assert result.status_code == 200
    assert result.attempts == 3
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_execute_exhausts_retries_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(504, json={"error": "down"})

    with pytest.raises(AppError) as exc:
        await _proxy(handler).execute(_make_tool(), {})
    assert exc.value.status_code == 502
    assert exc.value.code == "TOOL_UPSTREAM_ERROR"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_execute_does_not_retry_on_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"error": "not found"})

    with pytest.raises(AppError) as exc:
        await _proxy(handler).execute(_make_tool(), {})
    assert exc.value.status_code == 502
    assert calls["n"] == 1


# -- circuit breaker ----------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_five_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_sleep(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    proxy = _proxy(handler)
    tool = _make_tool()
    for _ in range(5):
        with pytest.raises(AppError) as exc:
            await proxy.execute(tool, {})
        assert exc.value.status_code == 502
    with pytest.raises(AppError) as exc:
        await proxy.execute(tool, {})
    assert exc.value.status_code == 503
    assert exc.value.code == "TOOL_CIRCUIT_OPEN"


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_sleep(monkeypatch)
    now = [1000.0]
    monkeypatch.setattr("app.services.tool_proxy.time.monotonic", lambda: now[0])
    state = {"ok": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["ok"]:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(503, json={"error": "down"})

    proxy = _proxy(handler)
    tool = _make_tool()
    for _ in range(5):
        with pytest.raises(AppError):
            await proxy.execute(tool, {})
    with pytest.raises(AppError) as exc:
        await proxy.execute(tool, {})
    assert exc.value.status_code == 503

    # After the recovery window, a single half-open trial is admitted.
    now[0] += 61.0
    state["ok"] = True
    result = await proxy.execute(tool, {})
    assert result.status_code == 200


# -- debug -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_returns_status_and_body_even_on_4xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    result = await _proxy(handler).debug(_make_tool(), {"a": 1})
    assert result.status_code == 404
    assert result.body == {"error": "not found"}
