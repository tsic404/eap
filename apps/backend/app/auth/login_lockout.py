"""Login failure lockout for local accounts (C-112).

Five consecutive password failures lock one login identifier — the (tenant,
email) pair, whether or not that email has an account — for fifteen minutes,
and while locked the password is not verified at all. Counting lives in Redis so
every instance shares one view (C-112 reuses C-10's shared-storage rule), with a
cooldown-guarded in-memory fallback so an outage degrades to per-instance
counting instead of switching the lockout off.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

import redis.asyncio as aioredis
import structlog
from redis.exceptions import RedisError

log = structlog.get_logger(__name__)

MAX_FAILURES = 5
LOCK_SECONDS = 900

_FAILURE_KEY = "login:fail:"
_LOCK_KEY = "login:lock:"

# Atomic failure record. KEYS[1] counts the failures inside the current window,
# KEYS[2] is the lock marker; ``ARGV[1]`` is the window/lock length in seconds,
# ``ARGV[2]`` the failure threshold. Reaching the threshold turns the count into
# the lock and drops the counter, so the identifier starts clean once the lock
# has run out. Returns whether the identifier is still unlocked.
_RECORD_FAILURE_LUA = """
local failures = redis.call('INCR', KEYS[1])
if failures >= tonumber(ARGV[2]) then
  redis.call('DEL', KEYS[1])
  redis.call('SET', KEYS[2], '1', 'EX', ARGV[1])
  return 0
end
redis.call('EXPIRE', KEYS[1], ARGV[1])
return 1
"""


class LoginAttemptStore(Protocol):
    """Minimal contract the login path needs from a lockout backend."""

    async def is_locked(self, identifier: str) -> bool:
        """Whether ``identifier`` is inside a lock window."""
        ...

    async def record_failure(self, identifier: str) -> None:
        """Count one failed attempt, locking the identifier at the threshold."""
        ...

    async def clear(self, identifier: str) -> None:
        """Forget the failures of ``identifier`` (a successful login)."""
        ...

    async def aclose(self) -> None:
        """Release any backend resources."""


class InMemoryLoginAttempts:
    """Per-process attempt counter — the no-Redis fallback.

    Entries are evicted once their window has passed, mirroring the Redis keys'
    TTL so a long outage cannot grow the maps without bound.
    """

    _SWEEP_INTERVAL_SECONDS = 60.0

    def __init__(
        self,
        *,
        max_failures: int = MAX_FAILURES,
        lock_seconds: float = LOCK_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_failures = max_failures
        self._lock_seconds = lock_seconds
        self._clock = clock
        # identifier -> (failure count, window expiry)
        self._failures: dict[str, tuple[int, float]] = {}
        # identifier -> lock expiry
        self._locks: dict[str, float] = {}
        self._last_sweep = 0.0

    async def is_locked(self, identifier: str) -> bool:
        now = self._clock()
        expiry = self._locks.get(identifier)
        if expiry is None:
            return False
        if expiry <= now:
            del self._locks[identifier]
            return False
        return True

    async def record_failure(self, identifier: str) -> None:
        now = self._clock()
        self._sweep(now)
        count = self._failures.get(identifier, (0, now))[0] + 1
        if count >= self._max_failures:
            self._failures.pop(identifier, None)
            self._locks[identifier] = now + self._lock_seconds
            return
        self._failures[identifier] = (count, now + self._lock_seconds)

    async def clear(self, identifier: str) -> None:
        self._failures.pop(identifier, None)
        self._locks.pop(identifier, None)

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < self._SWEEP_INTERVAL_SECONDS:
            return
        self._last_sweep = now
        for key, (_, expiry) in list(self._failures.items()):
            if expiry <= now:
                del self._failures[key]
        for key, expiry in list(self._locks.items()):
            if expiry <= now:
                del self._locks[key]

    async def aclose(self) -> None:
        self._failures.clear()
        self._locks.clear()


class RedisLoginAttempts:
    """Shared-storage attempt counter with a cooldown-guarded memory fallback.

    A transient Redis failure flips the store to its per-process fallback for a
    cooldown window and re-probes Redis on the next attempt after it, so a brief
    outage over-admits only for the cooldown length rather than for the process
    lifetime. A successful login is the one call that reaches the shared store
    inside that window, so the count it must clear cannot outlive the outage.
    """

    RECOVERY_RETRY_SECONDS = 30.0

    def __init__(
        self,
        redis: aioredis.Redis | None,
        *,
        max_failures: int = MAX_FAILURES,
        lock_seconds: int = LOCK_SECONDS,
    ) -> None:
        self._redis = redis
        self._max_failures = max_failures
        self._lock_seconds = lock_seconds
        self._fallback = InMemoryLoginAttempts(max_failures=max_failures, lock_seconds=lock_seconds)
        self._unavailable = redis is None
        self._failed_at = 0.0

    async def is_locked(self, identifier: str) -> bool:
        redis = self._redis
        if redis is None or self._degraded():
            return await self._fallback.is_locked(identifier)
        try:
            locked = await redis.get(self._lock_key(identifier)) is not None
        except RedisError:
            self._mark_unavailable()
            return await self._fallback.is_locked(identifier)
        self._mark_recovered()
        return locked

    async def record_failure(self, identifier: str) -> None:
        redis = self._redis
        if redis is None or self._degraded():
            await self._fallback.record_failure(identifier)
            return
        try:
            await redis.eval(
                _RECORD_FAILURE_LUA,
                2,
                self._failure_key(identifier),
                self._lock_key(identifier),
                self._lock_seconds,
                self._max_failures,
            )
        except RedisError:
            self._mark_unavailable()
            await self._fallback.record_failure(identifier)
            return
        self._mark_recovered()

    async def clear(self, identifier: str) -> None:
        """Forget the identifier's failures in both views (a successful login).

        The shared delete is attempted even inside the cooldown window: the
        window records that commands failed recently, not that the store is
        still gone, and C-112's cleared count must hold for every instance. The
        attempt is best effort — a failure is logged and leaves the successful
        login untouched — and a delete that gets through also ends the window.
        """
        redis = self._redis
        if redis is not None:
            try:
                await redis.delete(self._failure_key(identifier), self._lock_key(identifier))
            except RedisError:
                self._mark_unavailable()
                log.warning(
                    "login_lockout_clear_deferred",
                    resource="login_lockout",
                    resourceId="redis",
                    identifier=identifier,
                    retry_in_seconds=self.RECOVERY_RETRY_SECONDS,
                )
            else:
                self._mark_recovered()
        await self._fallback.clear(identifier)

    async def aclose(self) -> None:
        await self._fallback.aclose()

    def _degraded(self) -> bool:
        """Whether attempts are inside the post-failure cooldown window."""
        return (
            self._unavailable and time.monotonic() - self._failed_at < self.RECOVERY_RETRY_SECONDS
        )

    def _mark_unavailable(self) -> None:
        if not self._unavailable:
            log.warning(
                "login_lockout_redis_unavailable",
                resource="login_lockout",
                resourceId="redis",
                retry_in_seconds=self.RECOVERY_RETRY_SECONDS,
            )
        self._unavailable = True
        self._failed_at = time.monotonic()

    def _mark_recovered(self) -> None:
        if self._unavailable:
            log.info("login_lockout_redis_recovered", resource="login_lockout", resourceId="redis")
        self._unavailable = False
        self._failed_at = 0.0

    @staticmethod
    def _failure_key(identifier: str) -> str:
        return f"{_FAILURE_KEY}{identifier}"

    @staticmethod
    def _lock_key(identifier: str) -> str:
        return f"{_LOCK_KEY}{identifier}"
