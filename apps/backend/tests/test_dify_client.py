"""DifyClientService unit tests (httpx MockTransport — no live Dify required)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from app.core.exceptions import DifyApiError
from app.services.dify_client import DifyClientService

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> DifyClientService:
    return DifyClientService(
        "http://dify.example",
        "secret-api-key",
        transport=httpx.MockTransport(handler),
    )


def _freeze_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise exponential backoff so retry tests run without wall-clock delays."""

    async def _noop_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.services.dify_client.asyncio.sleep", _noop_sleep)


class _FlakyStream(httpx.AsyncByteStream):
    """An SSE body stream that fails mid-read after yielding one chunk."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"event: message\n"
        raise httpx.ReadTimeout("read timed out")


class _FlakyTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_FlakyStream(), request=request)


class _HalfOpenTransport(httpx.AsyncBaseTransport):
    """Fail the first five requests, then suspend the sixth (trial) on a gate."""

    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self._entered = entered
        self._release = release
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= 5:
            return httpx.Response(502, content=b"bad gateway")
        # The trial request parks here until the test releases it, keeping the
        # half-open reservation observable to a concurrent caller.
        self._entered.set()
        await self._release.wait()
        return httpx.Response(200, json={"ok": True})


@pytest.mark.asyncio
async def test_get_returns_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/info"
        return httpx.Response(200, json={"name": "test-app"})

    client = _client(handler)
    try:
        assert await client.get("/v1/info") == {"name": "test-app"}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_post_sends_json_body_and_returns_json() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "msg-1"})

    client = _client(handler)
    try:
        result = await client.post("/v1/chat-messages", {"query": "hello"})
        assert result == {"id": "msg-1"}
        assert captured["path"] == "/v1/chat-messages"
        assert captured["body"] == {"query": "hello"}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_retries_on_502_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(502, content=b"bad gateway")
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    try:
        assert await client.get("/v1/info") == {"ok": True}
        assert calls["n"] == 2
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_exhausts_retries_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(502, content=b"bad gateway")

    client = _client(handler)
    try:
        with pytest.raises(DifyApiError) as exc_info:
            await client.get("/v1/info")
        assert exc_info.value.status_code == 502
        assert calls["n"] == 4  # 3 retries + 1 initial attempt
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_does_not_retry_on_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"code": "bad_request"})

    client = _client(handler)
    try:
        with pytest.raises(DifyApiError) as exc_info:
            await client.get("/v1/info")
        assert exc_info.value.status_code == 400
        assert calls["n"] == 1  # client error is not retried
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_timeout_raises_504_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("timed out")

    client = _client(handler)
    try:
        with pytest.raises(DifyApiError) as exc_info:
            await client.get("/v1/info")
        assert exc_info.value.status_code == 504
        assert calls["n"] == 4
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_five_consecutive_failures() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(502, content=b"bad gateway")

    client = _client(handler)
    try:
        for _ in range(5):
            with pytest.raises(DifyApiError) as exc_info:
                await client.get("/v1/info", retries=0)
            assert exc_info.value.status_code == 502

        # The sixth call is rejected locally with 503 before reaching Dify.
        with pytest.raises(DifyApiError) as exc_info:
            await client.get("/v1/info", retries=0)
        assert exc_info.value.status_code == 503
        assert calls["n"] == 5
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("app.services.dify_client.time.monotonic", lambda: now[0])
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 5:
            return httpx.Response(502, content=b"bad gateway")
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    try:
        for _ in range(5):
            with pytest.raises(DifyApiError):
                await client.get("/v1/info", retries=0)

        # Still open: rejected locally.
        with pytest.raises(DifyApiError) as exc_info:
            await client.get("/v1/info", retries=0)
        assert exc_info.value.status_code == 503

        # Advance past the 60s window: half-open admits a trial request.
        now[0] += 61.0
        assert await client.get("/v1/info", retries=0) == {"ok": True}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_admits_single_trial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1000.0]
    monkeypatch.setattr("app.services.dify_client.time.monotonic", lambda: now[0])

    entered = asyncio.Event()
    release = asyncio.Event()
    transport = _HalfOpenTransport(entered, release)
    client = DifyClientService("http://dify.example", "secret-api-key", transport=transport)
    try:
        # Five consecutive failures open the circuit.
        for _ in range(5):
            with pytest.raises(DifyApiError):
                await client.get("/v1/info", retries=0)

        # Advance past the recovery window and launch the trial request; it is
        # admitted and then parked inside the transport.
        now[0] += 61.0
        trial = asyncio.create_task(client.get("/v1/info", retries=0))
        await asyncio.wait_for(entered.wait(), timeout=5)

        # A concurrent caller must be rejected while the trial is in flight.
        with pytest.raises(DifyApiError) as exc_info:
            await client.get("/v1/info", retries=0)
        assert exc_info.value.status_code == 503

        # Releasing the trial lets it succeed and close the circuit.
        release.set()
        assert await trial == {"ok": True}

        # The circuit is now closed: a fresh request succeeds.
        assert await client.get("/v1/info", retries=0) == {"ok": True}
    finally:
        release.set()
        await client.aclose()


@pytest.mark.asyncio
async def test_post_stream_yields_raw_bytes() -> None:
    sse = b'event: message\ndata: {"answer": "hi"}\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse,
            headers={"content-type": "text/event-stream"},
        )

    client = _client(handler)
    try:
        chunks = [c async for c in client.post_stream("/v1/chat-messages", {"query": "hi"})]
        assert b"".join(chunks) == sse
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_post_stream_error_status_records_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"bad gateway")

    client = _client(handler)
    try:
        with pytest.raises(DifyApiError) as exc_info:
            async for _ in client.post_stream("/v1/chat-messages", {"query": "hi"}):
                pass
        assert exc_info.value.status_code == 502
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_get_non_json_response_raises_502() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    client = _client(handler)
    try:
        with pytest.raises(DifyApiError) as exc_info:
            await client.get("/v1/info")
        assert exc_info.value.status_code == 502
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_post_stream_transport_error_raises_504() -> None:
    client = DifyClientService(
        "http://dify.example",
        "secret-api-key",
        transport=_FlakyTransport(),
    )
    try:
        with pytest.raises(DifyApiError) as exc_info:
            async for _ in client.post_stream("/v1/chat-messages", {"query": "hi"}):
                pass
        assert exc_info.value.status_code == 504
    finally:
        await client.aclose()
