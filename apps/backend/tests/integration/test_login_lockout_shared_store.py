"""C-112 lockout over a real shared store.

The store's degradation behaviour is asserted against a real Redis, because what
matters is what an outage leaves in the *shared* keys: the C-112 count a
successful login clears lives there, and the instance that skipped the clear
must not hand the next instance a stray streak.
"""

from __future__ import annotations

import uuid

import pytest
import redis.asyncio as aioredis

from app.auth.login_lockout import RedisLoginAttempts


@pytest.mark.asyncio
async def test_successful_login_inside_the_cooldown_clears_the_shared_store(
    redis_url: str, unreachable_redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Degraded → successful login → recovery → one more failure stays unlocked."""
    shared = aioredis.from_url(redis_url)
    unreachable = aioredis.from_url(unreachable_redis_url)
    attempts = RedisLoginAttempts(shared)
    identifier = f"{uuid.uuid4().hex}:acme.com"
    failure_key, lock_key = f"login:fail:{identifier}", f"login:lock:{identifier}"
    try:
        for _ in range(4):
            await attempts.record_failure(identifier)
        assert await shared.get(failure_key) == b"4"

        # One command fails: the store degrades and counts that attempt itself.
        monkeypatch.setattr(attempts, "_redis", unreachable)
        await attempts.record_failure(identifier)
        assert attempts._degraded() is True

        # The shared store answers again mid-cooldown and the user logs in with
        # the right password: success clears the count in both views.
        monkeypatch.setattr(attempts, "_redis", shared)
        assert attempts._degraded() is True
        await attempts.clear(identifier)

        assert attempts._degraded() is False
        assert await shared.exists(failure_key, lock_key) == 0

        # The streak restarts at one failure instead of locking at five.
        await attempts.record_failure(identifier)
        assert await attempts.is_locked(identifier) is False
        assert await shared.get(failure_key) == b"1"
    finally:
        await shared.delete(failure_key, lock_key)
        await attempts.aclose()
        await shared.aclose()
        await unreachable.aclose()
