"""Rate limiter tests: token buckets, rule table, middleware 429 behaviour."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.config import Settings
from app.middleware.security import RateLimitMiddleware
from app.rate_limit import (
    InMemoryTokenBucket,
    RateLimitRule,
    RedisTokenBucket,
    build_rules,
    matches_path,
)


def test_build_rules_matches_issue_spec() -> None:
    settings = Settings(_env_file=None)
    rules = build_rules(settings)

    by_name = {rule.name: rule for rule in rules}
    assert set(by_name) == {"global", "login", "conversation", "upload"}

    global_rule = by_name["global"]
    assert global_rule.capacity == 100
    assert global_rule.refill_per_second == 100.0
    assert not global_rule.per_user
    assert global_rule.path_prefixes == ()

    login = by_name["login"]
    assert login.capacity == 10
    assert login.refill_per_second == pytest.approx(10 / 60)
    assert not login.per_user

    conversation = by_name["conversation"]
    assert conversation.capacity == 20
    assert conversation.per_user

    upload = by_name["upload"]
    assert upload.capacity == 10
    assert upload.per_user


def test_matches_path_prefixes() -> None:
    prefixes = ("/api/conversations",)
    assert matches_path("/api/conversations", prefixes)
    assert matches_path("/api/conversations/abc", prefixes)
    assert not matches_path("/api/conversation", prefixes)
    assert not matches_path("/api/conversationships", prefixes)


def test_bucket_key_uses_user_for_per_user_rules() -> None:
    global_rule = RateLimitRule(name="global", capacity=100, refill_per_second=100.0)
    conversation_rule = RateLimitRule(
        name="conversation",
        capacity=20,
        refill_per_second=20 / 60,
        path_prefixes=("/api/conversations",),
        per_user=True,
    )
    assert (
        RateLimitMiddleware._bucket_key(global_rule, "1.2.3.4", "user-9")
        == "ratelimit:global:1.2.3.4"
    )
    assert (
        RateLimitMiddleware._bucket_key(conversation_rule, "1.2.3.4", "user-9")
        == "ratelimit:conversation:user-9"
    )
    # Anonymous falls back to the client IP.
    assert (
        RateLimitMiddleware._bucket_key(conversation_rule, "1.2.3.4", None)
        == "ratelimit:conversation:1.2.3.4"
    )


@pytest.mark.asyncio
async def test_in_memory_bucket_bursts_then_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    bucket = InMemoryTokenBucket()
    assert await bucket.acquire("k", capacity=2, refill_per_second=0.0)
    assert await bucket.acquire("k", capacity=2, refill_per_second=0.0)
    assert not await bucket.acquire("k", capacity=2, refill_per_second=0.0)


@pytest.mark.asyncio
async def test_in_memory_bucket_evicts_idle_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    bucket = InMemoryTokenBucket()
    clock = [0.0]
    monkeypatch.setattr("app.rate_limit.time.monotonic", lambda: clock[0])

    await bucket.acquire("idle", capacity=2, refill_per_second=1.0)  # window = 2s
    assert "idle" in bucket._buckets

    # Idle far past both the sweep interval and the refill window.
    clock[0] = 100.0
    await bucket.acquire("fresh", capacity=2, refill_per_second=1.0)

    assert "idle" not in bucket._buckets
    assert "fresh" in bucket._buckets


@pytest.mark.asyncio
async def test_redis_bucket_recovers_after_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = RedisTokenBucket("redis://localhost:6379/0")
    clock = [0.0]
    monkeypatch.setattr("app.rate_limit.time.monotonic", lambda: clock[0])

    calls = {"n": 0}

    async def fake_eval(key: str, capacity: int, refill_per_second: float) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RedisError("boom")
        return True

    monkeypatch.setattr(limiter, "_eval", fake_eval)

    # First failure flips the limiter to its in-memory fallback.
    assert await limiter.acquire("k", 2, 1.0) is True
    assert limiter._unavailable is True
    assert calls["n"] == 1

    # Within the cooldown window no Redis probe happens.
    clock[0] = 10.0
    assert await limiter.acquire("k", 2, 1.0) is True
    assert calls["n"] == 1

    # After the cooldown a probe succeeds and resets the shared path.
    clock[0] = 40.0
    assert await limiter.acquire("k", 2, 1.0) is True
    assert calls["n"] == 2
    assert limiter._unavailable is False
    assert limiter._failed_at == 0.0


def test_middleware_returns_429_when_conversation_limit_exceeded() -> None:
    settings = Settings(_env_file=None, rate_limit_conversation_per_minute=2)
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, settings=settings, limiter=InMemoryTokenBucket())

    @app.get("/api/conversations")
    def conversation() -> dict[str, str]:
        return {"ok": "yes"}

    with TestClient(app) as client:
        assert client.get("/api/conversations").status_code == 200
        assert client.get("/api/conversations").status_code == 200
        # Per-user keying falls back to the client IP here (TestClient sets no
        # JWT identity), which still exercises the per-route bucket.
        resp = client.get("/api/conversations")
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "RATE_LIMITED"


def test_middleware_returns_429_on_global_exhaustion() -> None:
    settings = Settings(_env_file=None, rate_limit_requests=2, rate_limit_window_seconds=3600)
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, settings=settings, limiter=InMemoryTokenBucket())

    @app.get("/api/anything")
    def anything() -> dict[str, str]:
        return {"ok": "yes"}

    with TestClient(app) as client:
        assert client.get("/api/anything").status_code == 200
        assert client.get("/api/anything").status_code == 200
        resp = client.get("/api/anything")
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "RATE_LIMITED"


def _redis_available() -> bool:
    import redis

    try:
        return bool(
            redis.Redis.from_url("redis://localhost:6379/0", socket_connect_timeout=1).ping()
        )
    except redis.RedisError:
        return False


@pytest.mark.skipif(not _redis_available(), reason="Redis not available")
@pytest.mark.asyncio
async def test_redis_bucket_grants_then_rejects() -> None:
    limiter = RedisTokenBucket("redis://localhost:6379/0")
    # Unique key so a prior run's bucket (TTL in the thousands of seconds) can
    # not leak tokens into this assertion.
    key = f"ratelimit:test:{uuid.uuid4().hex}"
    try:
        assert await limiter.acquire(key, capacity=2, refill_per_second=0.001)
        assert await limiter.acquire(key, capacity=2, refill_per_second=0.001)
        assert not await limiter.acquire(key, capacity=2, refill_per_second=0.001)
    finally:
        await limiter.aclose()
