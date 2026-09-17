"""Redis-backed token-bucket rate limiting with an in-memory fallback.

A token bucket allows a short burst (its capacity) while enforcing a sustained
average (its refill rate). The check-and-consume is atomic in Redis via a Lua
script, so multiple backend workers share one counter per client; when Redis is
unreachable the limiter degrades to a per-process in-memory bucket, preserving
limit enforcement for single-worker deployments and test runs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import redis.asyncio as aioredis
import structlog
from redis.exceptions import RedisError

from app.config import Settings

log = structlog.get_logger(__name__)

# Atomic token-bucket consume. KEYS[1] holds the current token count, KEYS[2]
# the last refill timestamp. Returns 1 when a token is granted, 0 otherwise.
#
# ``now`` is read from Redis' own clock (``TIME``) rather than passed in by the
# caller, so workers with skewed clocks cannot distort the shared bucket.
_TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local refill_per_second = tonumber(ARGV[2])
local now = redis.call('TIME')
local now_seconds = tonumber(now[1]) + tonumber(now[2]) / 1000000
local tokens = tonumber(redis.call('GET', KEYS[1]))
local last = tonumber(redis.call('GET', KEYS[2]))
if tokens == nil then
  tokens = capacity
  last = now_seconds
end
local new_tokens = math.min(capacity, tokens + (now_seconds - last) * refill_per_second)
if new_tokens < 1 then
  redis.call('SET', KEYS[1], new_tokens)
  redis.call('SET', KEYS[2], now_seconds)
  return 0
end
new_tokens = new_tokens - 1
redis.call('SET', KEYS[1], new_tokens)
redis.call('SET', KEYS[2], now_seconds)
local ttl = math.ceil(capacity / refill_per_second) + 1
redis.call('EXPIRE', KEYS[1], ttl)
redis.call('EXPIRE', KEYS[2], ttl)
return 1
"""


class TokenBucket(Protocol):
    """Minimal contract the middleware needs from a token-bucket backend."""

    async def acquire(self, key: str, capacity: int, refill_per_second: float) -> bool:
        """Reserve one token from ``key``; False when the bucket is empty."""
        ...

    async def aclose(self) -> None:
        """Release any backend resources."""


class InMemoryTokenBucket:
    """Per-process token bucket — the no-Redis fallback.

    Bucket entries are evicted once they sit idle longer than their refill
    window (``capacity / refill_per_second``), mirroring the Redis path's TTL so
    memory stays bounded during an outage.
    """

    _SWEEP_INTERVAL_SECONDS = 60.0

    def __init__(self) -> None:
        # (tokens, last_refill_monotonic, idle_seconds)
        self._buckets: dict[str, tuple[float, float, float]] = {}
        self._last_sweep = 0.0

    async def acquire(self, key: str, capacity: int, refill_per_second: float) -> bool:
        now = time.monotonic()
        self._sweep(now)
        idle_seconds = capacity / refill_per_second if refill_per_second > 0 else float("inf")
        entry = self._buckets.get(key)
        if entry is None:
            self._buckets[key] = (float(capacity) - 1.0, now, idle_seconds)
            return True
        tokens, last, _ = entry
        tokens = min(float(capacity), tokens + (now - last) * refill_per_second)
        if tokens < 1.0:
            self._buckets[key] = (tokens, now, idle_seconds)
            return False
        self._buckets[key] = (tokens - 1.0, now, idle_seconds)
        return True

    def _sweep(self, now: float) -> None:
        # Bound memory by periodically dropping buckets that have been idle
        # longer than their refill window. Runs at most once per interval.
        if now - self._last_sweep < self._SWEEP_INTERVAL_SECONDS:
            return
        self._last_sweep = now
        for key, (_, last, idle_seconds) in list(self._buckets.items()):
            if now - last > idle_seconds:
                del self._buckets[key]

    async def aclose(self) -> None:
        self._buckets.clear()


class RedisTokenBucket:
    """Redis token bucket with a cooldown-guarded in-memory fallback.

    A transient Redis failure flips the limiter to its in-memory fallback for a
    cooldown window. After the window the next acquire re-probes Redis and, on
    success, resets to the shared bucket — so a brief outage degrades per-worker
    over-admission only for the cooldown length rather than the process lifetime.
    """

    RECOVERY_RETRY_SECONDS = 30.0

    def __init__(self, redis_url: str) -> None:
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._fallback = InMemoryTokenBucket()
        self._unavailable = False
        self._failed_at = 0.0

    async def acquire(self, key: str, capacity: int, refill_per_second: float) -> bool:
        if self._unavailable and time.monotonic() - self._failed_at < self.RECOVERY_RETRY_SECONDS:
            return await self._fallback.acquire(key, capacity, refill_per_second)
        try:
            granted = await self._eval(key, capacity, refill_per_second)
        except RedisError:
            if not self._unavailable:
                log.warning(
                    "rate_limit_redis_unavailable",
                    resource="rate_limit",
                    resourceId="redis",
                    retry_in_seconds=self.RECOVERY_RETRY_SECONDS,
                )
            self._unavailable = True
            self._failed_at = time.monotonic()
            return await self._fallback.acquire(key, capacity, refill_per_second)
        if self._unavailable:
            log.info("rate_limit_redis_recovered", resource="rate_limit", resourceId="redis")
        self._unavailable = False
        self._failed_at = 0.0
        return granted

    async def _eval(self, key: str, capacity: int, refill_per_second: float) -> bool:
        result = await self._redis.eval(
            _TOKEN_BUCKET_LUA,
            2,
            key,
            f"{key}:ts",
            capacity,
            refill_per_second,
        )
        return bool(result)

    async def aclose(self) -> None:
        await self._redis.aclose()
        await self._fallback.aclose()


@dataclass(frozen=True)
class RateLimitRule:
    """A single limit: a token-bucket capacity/refill applied to a path prefix."""

    name: str
    capacity: int
    refill_per_second: float
    path_prefixes: tuple[str, ...] = ()
    # ``True`` keys the bucket by authenticated user id (falling back to the
    # client IP); ``False`` always keys by client IP.
    per_user: bool = False


# Conventional REST paths for the per-route limits. The auth / conversation /
# upload routers land in separate tasks; the prefixes below match the platform
# resource naming (``/api/<plural-noun>``) so they apply the moment those routes
# are registered.
_LOGIN_PREFIXES = ("/api/auth/login",)
_CONVERSATION_PREFIXES = ("/api/conversations", "/api/chat-messages")
_UPLOAD_PREFIXES = ("/api/uploads", "/api/files")


def build_rules(settings: Settings) -> list[RateLimitRule]:
    """Assemble the ordered rule table from settings.

    The global rule is listed first so every request is checked against it
    before the narrower per-route rules.
    """
    window = max(settings.rate_limit_window_seconds, 1)
    rules: list[RateLimitRule] = [
        RateLimitRule(
            name="global",
            capacity=settings.rate_limit_requests,
            refill_per_second=settings.rate_limit_requests / window,
        ),
        RateLimitRule(
            name="login",
            capacity=settings.rate_limit_login_per_minute,
            refill_per_second=settings.rate_limit_login_per_minute / 60.0,
            path_prefixes=_LOGIN_PREFIXES,
        ),
        RateLimitRule(
            name="conversation",
            capacity=settings.rate_limit_conversation_per_minute,
            refill_per_second=settings.rate_limit_conversation_per_minute / 60.0,
            path_prefixes=_CONVERSATION_PREFIXES,
            per_user=True,
        ),
        RateLimitRule(
            name="upload",
            capacity=settings.rate_limit_upload_per_minute,
            refill_per_second=settings.rate_limit_upload_per_minute / 60.0,
            path_prefixes=_UPLOAD_PREFIXES,
            per_user=True,
        ),
    ]
    return [rule for rule in rules if rule.capacity > 0 and rule.refill_per_second > 0]


def matches_path(path: str, prefixes: tuple[str, ...]) -> bool:
    """Whether ``path`` equals a prefix or lives under one (``prefix/…``)."""
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)
