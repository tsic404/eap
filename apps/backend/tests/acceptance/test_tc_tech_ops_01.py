"""Acceptance case for the health endpoints: liveness, entry health and readiness 503.

The three endpoints are unauthenticated raw responses. Liveness and the entry's
service information stay 200 whatever the dependencies do; the readiness probe
treats the database, the cache and the inference engine as readiness conditions,
probes them in parallel under per-probe time bounds, answers 503 with the failing
entries flagged, and reports the engine entry ready only when the engine answers
*and* its workspace has a model provider that can actually serve models.

Dependencies are replaced only at their real boundaries: the database/cache URLs
and the engine's HTTP base URL point at either the container fixtures or real
local sockets that refuse/stall, and the console the app installs at startup is
driven by a double of the console client. Every assertion therefore rests on the
HTTP response alone.
"""

import asyncio
import contextlib
import http
import socket
import threading
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.dify_console import DifyConsoleClient, DifyConsoleError

TC_ID = "TC-tech-ops-01"

# C-17 binds every dependency probe to 2s and runs the three of them in parallel;
# C-18 puts the provider judgement of the engine probe under its own 5s bound. The
# caps are the contract, the margin is what a loaded runner may add between the
# deadline firing and the response leaving the process.
_PROBE_CAP_SECONDS = 2.0
_PROVIDER_CAP_SECONDS = 5.0
_SCHEDULING_MARGIN_SECONDS = 0.5
# Two probes count as dispatched together when they are issued within this much of
# each other; a serial arrangement staggers them by whole probe bounds.
_PROBE_FANOUT_WINDOW_SECONDS = 1.0

# Console listings, shaped like the engine's workspace provider listing: every
# installed provider plugin appears, credited or not.
_USABLE_PROVIDER = {
    "provider": "langgenius/openai/openai",
    "preferred_provider_type": "custom",
    "custom_configuration": {
        "status": "active",
        "custom_models": [{"model": "gpt-4o-mini", "model_type": "llm"}],
    },
}
_UNCREDENTIALLED_PROVIDER = {
    "provider": "langgenius/ollama/ollama",
    "preferred_provider_type": "custom",
    "custom_configuration": {"status": "no-configure", "custom_models": []},
    "system_configuration": {"enabled": False},
}
_HOSTED_PROVIDER_WITH_VALID_QUOTA = {
    "provider": "langgenius/openai/openai",
    "preferred_provider_type": "system",
    "custom_configuration": {"status": "no-configure", "custom_models": []},
    "system_configuration": {
        "enabled": True,
        "current_quota_type": "trial",
        "quota_configurations": [{"quota_type": "trial", "is_valid": True}],
    },
}
_HOSTED_PROVIDER_WITH_INVALID_QUOTA = {
    "provider": "langgenius/openai/openai",
    "preferred_provider_type": "system",
    "custom_configuration": {"status": "no-configure", "custom_models": []},
    "system_configuration": {
        "enabled": True,
        "current_quota_type": "trial",
        "quota_configurations": [{"quota_type": "trial", "is_valid": False}],
    },
}


def _listing(*providers: Any) -> dict[str, Any]:
    return {"data": list(providers)}


def _console(reply: Any = None, *, failure: BaseException | None = None, stalls: bool = False):
    """A console double whose provider listing is driven by the test.

    A stalled listing never answers, so only the probe's own time bound can end it.
    """
    console = AsyncMock(spec=DifyConsoleClient)
    if stalls:

        async def _never() -> Any:
            await asyncio.Event().wait()

        console.get_model_providers.side_effect = _never
    elif failure is not None:
        console.get_model_providers.side_effect = failure
    else:
        console.get_model_providers.return_value = reply
    return console


def _refused_endpoint() -> str:
    """A local ``host:port`` with nothing listening — connects are refused at once."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{sock.getsockname()[1]}"


class _StallingEndpoint:
    """A local ``host:port`` that completes the TCP handshake and then stays mute.

    Stands in for a dependency that accepts connections but never answers, so a
    probe can only finish by running out of its own patience. Every accepted
    connection is stamped, which is how a case tells a parallel fan-out (the three
    probes contacting their dependencies at once) from a serial one.
    """

    def __init__(self) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self._listener.settimeout(0.1)
        self._held: list[socket.socket] = []
        self._done = threading.Event()
        self._acceptor: threading.Thread | None = None
        self.arrivals: list[float] = []

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self._listener.getsockname()[1]}"

    def _accept(self) -> None:
        while not self._done.is_set():
            try:
                connection, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.arrivals.append(time.monotonic())
            # Held open: closing here would let a probe fail at once instead of
            # running out of its own time bound.
            self._held.append(connection)

    def __enter__(self) -> "_StallingEndpoint":
        self._acceptor = threading.Thread(target=self._accept, daemon=True)
        self._acceptor.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._done.set()
        if self._acceptor is not None:
            self._acceptor.join(timeout=2.0)
        for connection in self._held:
            connection.close()
        self._listener.close()


@contextlib.asynccontextmanager
async def _engine_endpoint(status: int = 200) -> AsyncIterator[str]:
    """A real HTTP endpoint standing in for the engine's health route."""

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            body = b'{"status":"ok"}'
            writer.write(
                b"HTTP/1.1 %d %s\r\n" % (status, http.HTTPStatus(status).phrase.encode())
                + b"Content-Type: application/json\r\n"
                + b"Content-Length: %d\r\n" % len(body)
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(_handle, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()


def _dead_database(host: str) -> str:
    return f"postgresql+asyncpg://eap:eap_password@{host}/eap"


def _set_engine(api, base_url: str, console: Any) -> None:
    """Point the running app at ``base_url``/``console`` without touching the fixtures."""
    api.app.state.settings = api.settings.model_copy(update={"dify_api_base_url": base_url})
    api.app.state.dify_console = console


@pytest.mark.asyncio
async def test_service_info_is_raw_200_without_authentication(api) -> None:
    resp = await api.client.get("/api/health")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"service", "version", "status"}
    assert body["service"] == "eap-backend"
    assert body["status"] == "ok"
    # C-17 fixes no version value: the field only carries a non-empty one.
    assert isinstance(body["version"], str) and body["version"]
    # Probes are the raw-response exception: nothing is wrapped in the data envelope.
    assert "data" not in body


@pytest.mark.asyncio
async def test_service_info_stays_raw_200_while_dependencies_are_down(api) -> None:
    refused = _refused_endpoint()
    api.app.state.settings = api.settings.model_copy(
        update={
            "database_url": _dead_database(refused),
            "redis_url": f"redis://{refused}/0",
            "dify_api_base_url": f"http://{refused}",
        }
    )

    resp = await api.client.get("/api/health")

    # The entry's service information is not a readiness condition of its own: it
    # reports the service and stays 200 however the dependencies behave.
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_liveness_probe_is_raw_200_while_dependencies_are_down(api) -> None:
    refused = _refused_endpoint()
    api.app.state.settings = api.settings.model_copy(
        update={
            "database_url": _dead_database(refused),
            "redis_url": f"redis://{refused}/0",
            "dify_api_base_url": f"http://{refused}",
        }
    )

    resp = await api.client.get("/api/health/live")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_probe_is_raw_200_when_all_three_dependencies_are_healthy(api) -> None:
    async with _engine_endpoint(200) as engine:
        _set_engine(api, engine, _console(_listing(_USABLE_PROVIDER)))
        resp = await api.client.get("/api/health/ready")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok", "dify": "ok"},
    }


@pytest.mark.asyncio
async def test_readiness_probe_answers_503_flagging_only_the_unreachable_database(api) -> None:
    async with _engine_endpoint(200) as engine:
        api.app.state.settings = api.settings.model_copy(
            update={
                "database_url": _dead_database(_refused_endpoint()),
                "dify_api_base_url": engine,
            }
        )
        api.app.state.dify_console = _console(_listing(_USABLE_PROVIDER))
        resp = await api.client.get("/api/health/ready")

    assert resp.status_code == 503
    # The failing entry is the only one flagged; the others keep their real state.
    assert resp.json() == {
        "status": "degraded",
        "checks": {"database": "error", "redis": "ok", "dify": "ok"},
    }


@pytest.mark.asyncio
async def test_readiness_probe_answers_503_flagging_only_the_unreachable_cache(api) -> None:
    async with _engine_endpoint(200) as engine:
        api.app.state.settings = api.settings.model_copy(
            update={
                "redis_url": f"redis://{_refused_endpoint()}/0",
                "dify_api_base_url": engine,
            }
        )
        api.app.state.dify_console = _console(_listing(_USABLE_PROVIDER))
        resp = await api.client.get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json() == {
        "status": "degraded",
        "checks": {"database": "ok", "redis": "error", "dify": "ok"},
    }


@pytest.mark.asyncio
async def test_readiness_probe_answers_503_flagging_the_unreachable_engine(api) -> None:
    _set_engine(api, f"http://{_refused_endpoint()}", _console(_listing(_USABLE_PROVIDER)))

    resp = await api.client.get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json() == {
        "status": "degraded",
        "checks": {"database": "ok", "redis": "ok", "dify": "error"},
    }


@pytest.mark.asyncio
async def test_readiness_probe_skips_the_console_when_the_engine_is_unreachable(api) -> None:
    console = _console(_listing(_USABLE_PROVIDER))
    _set_engine(api, f"http://{_refused_endpoint()}", console)

    resp = await api.client.get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json()["checks"]["dify"] == "error"
    # An engine that is already unreachable is not asked for its provider listing.
    assert console.get_model_providers.await_count == 0


@pytest.mark.asyncio
async def test_readiness_probe_flags_the_engine_when_its_health_endpoint_is_not_200(api) -> None:
    async with _engine_endpoint(503) as engine:
        _set_engine(api, engine, _console(_listing(_USABLE_PROVIDER)))
        resp = await api.client.get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json() == {
        "status": "degraded",
        "checks": {"database": "ok", "redis": "ok", "dify": "error"},
    }


@pytest.mark.asyncio
async def test_readiness_probe_flags_the_engine_when_the_workspace_lists_no_provider(api) -> None:
    async with _engine_endpoint(200) as engine:
        _set_engine(api, engine, _console(_listing()))
        resp = await api.client.get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json()["checks"]["dify"] == "error"


@pytest.mark.asyncio
async def test_readiness_probe_flags_the_engine_when_no_listed_provider_can_serve(api) -> None:
    async with _engine_endpoint(200) as engine:
        _set_engine(api, engine, _console(_listing(_UNCREDENTIALLED_PROVIDER)))
        resp = await api.client.get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json()["checks"]["dify"] == "error"


@pytest.mark.asyncio
async def test_readiness_probe_flags_the_engine_when_a_hosted_quota_is_invalid(api) -> None:
    async with _engine_endpoint(200) as engine:
        _set_engine(api, engine, _console(_listing(_HOSTED_PROVIDER_WITH_INVALID_QUOTA)))
        resp = await api.client.get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json()["checks"]["dify"] == "error"


@pytest.mark.asyncio
async def test_readiness_probe_accepts_a_usable_provider_among_unusable_ones(api) -> None:
    async with _engine_endpoint(200) as engine:
        _set_engine(
            api,
            engine,
            _console(_listing(_UNCREDENTIALLED_PROVIDER, _USABLE_PROVIDER)),
        )
        resp = await api.client.get("/api/health/ready")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok", "dify": "ok"},
    }


@pytest.mark.asyncio
async def test_readiness_probe_accepts_an_enabled_hosted_provider_with_a_valid_quota(api) -> None:
    async with _engine_endpoint(200) as engine:
        _set_engine(api, engine, _console(_listing(_HOSTED_PROVIDER_WITH_VALID_QUOTA)))
        resp = await api.client.get("/api/health/ready")

    assert resp.status_code == 200
    assert resp.json()["checks"]["dify"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(DifyConsoleError(401, "unauthorized"), id="credential-rejected"),
        pytest.param(httpx.ConnectError("console unreachable"), id="listing-query-failed"),
        pytest.param(TimeoutError("listing timed out"), id="listing-timed-out"),
    ],
)
async def test_readiness_probe_flags_the_engine_when_the_listing_cannot_be_read(
    api, failure: BaseException
) -> None:
    async with _engine_endpoint(200) as engine:
        _set_engine(api, engine, _console(failure=failure))
        resp = await api.client.get("/api/health/ready")

    # Judgement that cannot be obtained is never a pass: the probe stays negative.
    assert resp.status_code == 503
    assert resp.json() == {
        "status": "degraded",
        "checks": {"database": "ok", "redis": "ok", "dify": "error"},
    }


@pytest.mark.asyncio
async def test_readiness_probe_flags_the_engine_when_no_console_is_configured(api) -> None:
    async with _engine_endpoint(200) as engine:
        _set_engine(api, engine, None)
        resp = await api.client.get("/api/health/ready")

    assert resp.status_code == 503
    assert resp.json()["checks"]["dify"] == "error"


@pytest.mark.asyncio
async def test_readiness_probe_answers_within_the_parallel_probe_caps_while_everything_stalls(
    api,
) -> None:
    with _StallingEndpoint() as stalled:
        api.app.state.settings = api.settings.model_copy(
            update={
                "database_url": _dead_database(stalled.address),
                "redis_url": f"redis://{stalled.address}/0",
                "dify_api_base_url": f"http://{stalled.address}",
            }
        )
        api.app.state.dify_console = _console(_listing(_USABLE_PROVIDER))
        started = time.monotonic()
        resp = await asyncio.wait_for(api.client.get("/api/health/ready"), timeout=4.0)
        elapsed = time.monotonic() - started

    assert resp.status_code == 503
    assert resp.json() == {
        "status": "degraded",
        "checks": {"database": "error", "redis": "error", "dify": "error"},
    }
    # C-17: the three probes are dispatched together — every dependency is
    # contacted inside one window, not one probe bound after another.
    assert len(stalled.arrivals) >= 3, stalled.arrivals
    spread = max(stalled.arrivals) - min(stalled.arrivals)
    assert spread < _PROBE_FANOUT_WINDOW_SECONDS, stalled.arrivals
    # And each probe is bounded on its own, so a stack that stalls on every
    # dependency answers in about one 2s bound: a serial arrangement — in any
    # pairing — would need a bound per probe.
    assert elapsed < _PROBE_CAP_SECONDS + _SCHEDULING_MARGIN_SECONDS, elapsed


@pytest.mark.asyncio
async def test_readiness_probe_answers_within_the_provider_cap_when_the_listing_stalls(api) -> None:
    async with _engine_endpoint(200) as engine:
        _set_engine(api, engine, _console(stalls=True))
        started = time.monotonic()
        resp = await asyncio.wait_for(api.client.get("/api/health/ready"), timeout=7.0)
        elapsed = time.monotonic() - started

    assert resp.status_code == 503
    assert resp.json() == {
        "status": "degraded",
        "checks": {"database": "ok", "redis": "ok", "dify": "error"},
    }
    # C-18: the provider judgement carries its own 5s bound — above the raw 2s
    # probe bound the engine already answered within, and the whole probe must
    # stay inside it, not merely inside "not a hang".
    assert elapsed < _PROVIDER_CAP_SECONDS + _SCHEDULING_MARGIN_SECONDS, elapsed
