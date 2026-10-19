"""Acceptance case TC-tech-conv-03.

Sending to the conversation surface is limited to 20 requests per minute per
user: requests 1-20 pass, the 21st is refused with 429 ``RATE_LIMITED``, no
rate-limit response headers are emitted, the whole per-minute budget comes back
once the refill window has passed, and the bucket is keyed by user and path
domain rather than by client IP.

The app under test is built with rate limiting enabled and shares the API
fixture's database and Redis container, so the limiter counts against the real
shared store the multi-instance deployment relies on. The limiter's clock is
injected, so no assertion depends on how long the requests happen to take.

C-10 also requires the counter to live in a store shared across instances and
to degrade to single-instance counting, retried after 30 seconds, when that
store is unreachable. The outage case points two app instances at a switchable
proxy in front of the real Redis container, so it observes the degradation and
the return to shared counting from HTTP responses alone.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.rate_limit import _TOKEN_BUCKET_LUA, RedisTokenBucket
from app.services.conversation import ConversationService

TC_ID = "TC-tech-conv-03"

_CONVERSATIONS = "/api/conversations"
# The upload surface (C-10: 10 requests per minute per user) is the second
# per-user path domain, so it is what tells a user+domain key apart from a
# user-only key.
_UPLOADS = "/api/files/upload"
_LIMIT = 20
_UPLOAD_LIMIT = 10
_REFILL_WINDOW_SECONDS = 60.0
# C-10 retries the shared store 30 seconds after it becomes unreachable; the
# extra margin absorbs scheduling delay between the failure and this process.
_OUTAGE_RETRY_SECONDS = 30.0


class _Clock:
    """A wall clock the case advances by hand."""

    def __init__(self) -> None:
        self.now = time.time()

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _clock_driven_script() -> str:
    """The production bucket script, with its ``TIME`` read taken from an argument.

    Production reads the current time from Redis itself so workers with skewed
    clocks cannot distort the shared bucket; a test cannot advance that clock,
    so the same script runs with the timestamp supplied by ``_Clock``. Anything
    else about the script — the atomic check-and-consume, the two keys and the
    TTL — stays production behaviour.
    """
    injected = _TOKEN_BUCKET_LUA.replace(
        "local now = redis.call('TIME')\n"
        "local now_seconds = tonumber(now[1]) + tonumber(now[2]) / 1000000",
        "local now_seconds = tonumber(ARGV[3])",
    )
    if injected == _TOKEN_BUCKET_LUA:
        raise AssertionError(
            "the token-bucket script no longer reads its clock through redis.call('TIME')"
        )
    return injected


class _ClockBucket(RedisTokenBucket):
    """The real Redis token bucket, refilling on the test's clock."""

    def __init__(self, redis_url: str, clock: _Clock) -> None:
        super().__init__(redis_url)
        self._clock = clock
        self._script = _clock_driven_script()

    async def _eval(self, key: str, capacity: int, refill_per_second: float) -> bool:
        result = await self._redis.eval(
            self._script,
            2,
            key,
            f"{key}:ts",
            capacity,
            refill_per_second,
            self._clock.now,
        )
        return bool(result)


@asynccontextmanager
async def _rate_limited_client(
    api, monkeypatch: pytest.MonkeyPatch, clock: _Clock, *, redis_url: str | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    """An app with rate limiting enabled over the fixture's database and Redis.

    The limiter is constructed with ``clock``, so refill is decided by the case
    and never by request latency. ``redis_url`` points it at a stand-in for the
    shared store, and every construction gets a bucket of its own, so two
    instances of the app never share a fallback counter.
    """
    store_url = redis_url or api.settings.redis_url
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=api.settings.database_url,
        redis_url=store_url,
        jwt_public_key=api.settings.jwt_public_key,
        rate_limit_enabled=True,
        # The global per-IP bucket is widened so it can never mask the per-user
        # conversation rule this case is about.
        rate_limit_requests=100_000,
    )
    monkeypatch.setattr(
        "app.main.RedisTokenBucket", lambda *args, **kwargs: _ClockBucket(store_url, clock)
    )
    app = create_app(settings)
    # The lifespan does not run under an ASGI transport, so wire the state the
    # routers resolve.
    app.state.conversation_service = ConversationService(settings)

    async def override_get_session() -> AsyncIterator[Any]:
        async with api.db.session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    # Every case uses its own synthetic client address. The global per-IP
    # bucket is shared Redis state, and the injected clock would otherwise be
    # carried into whatever request reuses the address next.
    octets = uuid.uuid4().int.to_bytes(16, "big")[-2:]
    transport = httpx.ASGITransport(
        app=app, raise_app_exceptions=False, client=(f"10.42.{octets[0]}.{octets[1]}", 12345)
    )
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        await app.state.rate_limiter.aclose()


class _SharedStoreProxy:
    """A TCP proxy in front of the shared counter store, switchable at runtime.

    Pointing the app under test here instead of at the container lets a case
    take the shared store away and bring it back without touching the
    container: while ``is_forwarding`` is false every accepted connection is
    closed, which the client observes as the store being unreachable.
    """

    def __init__(self, upstream_url: str) -> None:
        parsed = urlparse(upstream_url)
        if parsed.hostname is None or parsed.port is None:
            raise ValueError(f"shared store URL lacks a host and port: {upstream_url}")
        self._upstream = (parsed.hostname, parsed.port)
        self._server: asyncio.AbstractServer | None = None
        self._pumps: set[asyncio.Task[None]] = set()
        self.is_forwarding = False
        self.port = 0

    @property
    def url(self) -> str:
        return f"redis://127.0.0.1:{self.port}/0"

    async def __aenter__(self) -> "_SharedStoreProxy":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        sockets = self._server.sockets or ()
        if not sockets:
            raise RuntimeError("shared store proxy did not bind a socket")
        self.port = sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for pump in list(self._pumps):
            pump.cancel()
        await asyncio.gather(*self._pumps, return_exceptions=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not self.is_forwarding:
            writer.close()
            await writer.wait_closed()
            return
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(*self._upstream)
        except OSError:
            writer.close()
            await writer.wait_closed()
            return
        pumps = {
            asyncio.create_task(self._pump(reader, upstream_writer)),
            asyncio.create_task(self._pump(upstream_reader, writer)),
        }
        self._pumps.update(pumps)
        try:
            await asyncio.gather(*pumps, return_exceptions=True)
        finally:
            self._pumps.difference_update(pumps)

    @staticmethod
    async def _pump(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
        try:
            while chunk := await source.read(64 * 1024):
                target.write(chunk)
                await target.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            target.close()


async def _send_requests(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> list[httpx.Response]:
    """Fire the whole per-minute budget plus one excess request, in order."""
    return [await client.get(_CONVERSATIONS, headers=headers) for _ in range(_LIMIT + 1)]


def _crossed_limit(responses: list[httpx.Response]) -> httpx.Response:
    assert [resp.status_code for resp in responses[:_LIMIT]] == [200] * _LIMIT
    return responses[_LIMIT]


@pytest.mark.asyncio
async def test_one_to_twenty_requests_pass_and_the_twenty_first_is_rate_limited(
    api, monkeypatch
) -> None:
    tenant, user = await api.seed(role="employee")
    headers = api.auth(user, tenant)

    async with _rate_limited_client(api, monkeypatch, _Clock()) as client:
        responses = await _send_requests(client, headers)

    assert [resp.status_code for resp in responses[:_LIMIT]] == [200] * _LIMIT
    limited = responses[_LIMIT]
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_rate_limited_responses_carry_no_rate_limit_headers(api, monkeypatch) -> None:
    tenant, user = await api.seed(role="employee")
    headers = api.auth(user, tenant)

    async with _rate_limited_client(api, monkeypatch, _Clock()) as client:
        responses = await _send_requests(client, headers)

    allowed, limited = responses[_LIMIT - 1], _crossed_limit(responses)
    assert limited.status_code == 429
    for resp in (allowed, limited):
        names = {name.lower() for name in resp.headers}
        assert "retry-after" not in names
        assert not any(name.startswith("x-ratelimit") for name in names)


@pytest.mark.asyncio
async def test_the_whole_conversation_budget_returns_after_the_refill_window(
    api, monkeypatch
) -> None:
    tenant, user = await api.seed(role="employee")
    headers = api.auth(user, tenant)
    clock = _Clock()

    async with _rate_limited_client(api, monkeypatch, clock) as client:
        assert _crossed_limit(await _send_requests(client, headers)).status_code == 429

        # Twenty tokens per minute refill the bucket at one token every three
        # seconds; once that window has passed the whole per-minute budget is
        # back, so the next twenty requests pass and the twenty-first is
        # refused again. An implementation that returned a single token would
        # fail here.
        clock.advance(_REFILL_WINDOW_SECONDS + 5)
        recovered = await _send_requests(client, headers)

    assert [resp.status_code for resp in recovered[:_LIMIT]] == [200] * _LIMIT
    assert recovered[_LIMIT].status_code == 429


@pytest.mark.asyncio
async def test_the_conversation_limit_is_keyed_by_user_not_by_client_ip(api, monkeypatch) -> None:
    tenant, alice = await api.seed(role="employee")
    bob = await api.seed_user(tenant)

    async with _rate_limited_client(api, monkeypatch, _Clock()) as client:
        # Both users share one client IP over the ASGI transport.
        alice_responses = await _send_requests(client, api.auth(alice, tenant))
        assert _crossed_limit(alice_responses).status_code == 429
        bob_first = await client.get(_CONVERSATIONS, headers=api.auth(bob, tenant))

    assert bob_first.status_code == 200


@pytest.mark.asyncio
async def test_the_conversation_limit_is_keyed_by_user_and_path_domain(api, monkeypatch) -> None:
    tenant, user = await api.seed(role="employee")
    headers = api.auth(user, tenant)

    async with _rate_limited_client(api, monkeypatch, _Clock()) as client:
        # Exhaust this user's conversation bucket...
        assert _crossed_limit(await _send_requests(client, headers)).status_code == 429

        # ...then spend the upload domain's own per-user budget. Were the key
        # the user alone, the first upload request would already be refused as
        # the conversation bucket is empty; a separately keyed bucket grants
        # ten before its eleventh is refused.
        uploads = [await client.post(_UPLOADS, headers=headers) for _ in range(_UPLOAD_LIMIT + 1)]

    assert [resp.status_code for resp in uploads[:_UPLOAD_LIMIT]].count(429) == 0
    assert uploads[_UPLOAD_LIMIT].status_code == 429
    assert uploads[_UPLOAD_LIMIT].json()["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_shared_store_outage_degrades_and_returns_to_shared_counting(
    api, monkeypatch
) -> None:
    """A failed shared store must not surface as 500 and must recover in 30s.

    The negative case for C-10's degradation clause: with the shared store
    unreachable, requests that would have become 500 must instead be counted by
    the instance itself; a second instance keeps its own budget (single-instance
    counting); once the store is back, the counters must be shared again within
    the 30-second retry window.
    """
    tenant, user = await api.seed(role="employee")
    headers = api.auth(user, tenant)
    clock = _Clock()

    async with _SharedStoreProxy(api.settings.redis_url) as store:
        async with (
            _rate_limited_client(api, monkeypatch, clock, redis_url=store.url) as first,
            _rate_limited_client(api, monkeypatch, clock, redis_url=store.url) as second,
        ):
            # The first request on each instance hits the unreachable store and
            # records the failure; it must be served from the local counter.
            first_initial = await first.get(_CONVERSATIONS, headers=headers)
            first_failed_at = time.monotonic()
            second_initial = await second.get(_CONVERSATIONS, headers=headers)
            second_failed_at = time.monotonic()

            # The store comes back immediately after the outage is observed,
            # while both limiters are still inside their retry cooldown.
            store.is_forwarding = True

            first_rest = [await first.get(_CONVERSATIONS, headers=headers) for _ in range(_LIMIT)]
            second_rest = [await second.get(_CONVERSATIONS, headers=headers) for _ in range(_LIMIT)]

            # No request may surface as a 500 while the store is down, and each
            # instance still enforces the limit from its own counter.
            for initial, rest in ((first_initial, first_rest), (second_initial, second_rest)):
                assert 500 not in {initial.status_code, *(resp.status_code for resp in rest)}
                assert [resp.status_code for resp in rest[: _LIMIT - 1]] == [200] * (_LIMIT - 1)
                limited = rest[_LIMIT - 1]
                assert limited.status_code == 429
                assert limited.json()["error"]["code"] == "RATE_LIMITED"
            # Single-instance counting: the second instance still had its whole
            # budget after the first had spent its own.
            assert first_initial.status_code == 200
            assert second_initial.status_code == 200

            # Wait out C-10's 30-second retry window (measured from the failure,
            # which happened before the store came back) plus scheduling slack.
            deadline = max(first_failed_at, second_failed_at) + _OUTAGE_RETRY_SECONDS + 0.5
            await asyncio.sleep(max(0.0, deadline - time.monotonic()))

            # An instance that never saw the outage drains the shared bucket.
            async with _rate_limited_client(api, monkeypatch, clock, redis_url=store.url) as fresh:
                drained = await _send_requests(fresh, headers)
                assert drained[_LIMIT].status_code == 429

            # A 200 here would mean the degraded instance is still counting on
            # its own (its local bucket has refilled); 429 proves it is back on
            # the shared counter.
            recovered = await first.get(_CONVERSATIONS, headers=headers)

    assert recovered.status_code == 429
    assert recovered.json()["error"]["code"] == "RATE_LIMITED"
