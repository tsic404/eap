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
"""

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

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
    api, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> AsyncIterator[httpx.AsyncClient]:
    """An app with rate limiting enabled over the fixture's database and Redis.

    The limiter is constructed with ``clock``, so refill is decided by the case
    and never by request latency.
    """
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=api.settings.database_url,
        redis_url=api.settings.redis_url,
        jwt_public_key=api.settings.jwt_public_key,
        rate_limit_enabled=True,
        # The global per-IP bucket is widened so it can never mask the per-user
        # conversation rule this case is about.
        rate_limit_requests=100_000,
    )
    limiter = _ClockBucket(api.settings.redis_url, clock)
    monkeypatch.setattr("app.main.RedisTokenBucket", lambda *args, **kwargs: limiter)
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
