"""Local-account credential storage and the login lockout store.

The lockout's shipped policy (five failures, a fifteen-minute window) and its
shared-storage keys are asserted here against the defaults; the HTTP-level
judgement order lives in ``tests/acceptance/test_tc_tech_auth_04.py``.
"""

from __future__ import annotations

import uuid

import pytest
import redis.asyncio as aioredis

from app.auth.login_lockout import InMemoryLoginAttempts, RedisLoginAttempts
from app.auth.password import hash_password, verify_password

_PASSWORD = "correct-horse-battery-staple"


@pytest.mark.parametrize(
    ("password", "expected"),
    [
        (_PASSWORD, True),
        ("wrong-password", False),
        ("", False),
        (_PASSWORD.upper(), False),
        (_PASSWORD + " ", False),
    ],
)
def test_verify_password_matches_only_the_stored_password(password: str, expected: bool) -> None:
    assert verify_password(password, hash_password(_PASSWORD)) is expected


def test_stored_password_is_a_salted_slow_hash() -> None:
    """The encoding is a self-describing scrypt hash, never the password itself."""
    first, second = hash_password(_PASSWORD), hash_password(_PASSWORD)
    for encoded in (first, second):
        scheme, n, r, p, salt, digest = encoded.split("$")
        assert scheme == "scrypt"
        assert (int(n), int(r), int(p)) == (2**14, 8, 1)
        assert salt and digest
        assert _PASSWORD not in encoded
    # A fresh salt per call: equal passwords do not produce equal encodings.
    assert first != second


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        _PASSWORD,
        "bcrypt$12$abcdef",
        "scrypt$16384$8$1$not-base64!$also-bad",
        "scrypt$notanumber$8$1$YWJjZGVmZ2hpamtsbW5vcA==$YWJjZGVmZ2hpamtsbW5vcA==",
        "scrypt$99999999$8$1$YWJjZGVmZ2hpamtsbW5vcA==$YWJjZGVmZ2hpamtsbW5vcA==",
        "scrypt$16384$8$1$$",
    ],
)
def test_verify_password_rejects_unusable_encodings(encoded: str) -> None:
    assert verify_password(_PASSWORD, encoded) is False


@pytest.mark.asyncio
async def test_in_memory_lockout_defaults_lock_at_five_for_fifteen_minutes() -> None:
    """The shipped policy: the 5th failure locks, and the lock lasts 900 seconds."""
    clock = [0.0]
    attempts = InMemoryLoginAttempts(clock=lambda: clock[0])
    identifier = "tenant:user@acme.com"

    for _ in range(4):
        await attempts.record_failure(identifier)
        assert await attempts.is_locked(identifier) is False

    await attempts.record_failure(identifier)
    assert await attempts.is_locked(identifier) is True

    clock[0] = 899.0
    assert await attempts.is_locked(identifier) is True
    clock[0] = 900.0
    assert await attempts.is_locked(identifier) is False

    # The lock was converted from the count, so the next streak starts at zero.
    for _ in range(4):
        await attempts.record_failure(identifier)
    assert await attempts.is_locked(identifier) is False


@pytest.mark.asyncio
async def test_in_memory_lockout_clear_forgets_the_failures() -> None:
    attempts = InMemoryLoginAttempts()
    identifier = "tenant:user@acme.com"
    for _ in range(4):
        await attempts.record_failure(identifier)

    await attempts.clear(identifier)

    for _ in range(4):
        await attempts.record_failure(identifier)
    assert await attempts.is_locked(identifier) is False


@pytest.mark.asyncio
async def test_lockout_degrades_when_the_shared_store_is_unreachable(
    unreachable_redis_url: str,
) -> None:
    """A store outage must not switch the lockout off inside one instance."""
    client = aioredis.from_url(unreachable_redis_url)
    attempts = RedisLoginAttempts(client)
    try:
        for _ in range(4):
            await attempts.record_failure("tenant:user@acme.com")
            assert await attempts.is_locked("tenant:user@acme.com") is False
        await attempts.record_failure("tenant:user@acme.com")
        assert await attempts.is_locked("tenant:user@acme.com") is True
        assert await attempts.is_locked("tenant:other@acme.com") is False
        assert attempts._unavailable is True
    finally:
        await attempts.aclose()
        await client.aclose()


@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_shared_store_lockout_is_one_view_across_instances(local_redis_url: str) -> None:
    """The count and the lock live in shared storage with the C-112 window."""
    client = aioredis.from_url(local_redis_url)
    second_client = aioredis.from_url(local_redis_url)
    attempts = RedisLoginAttempts(client)
    other_instance = RedisLoginAttempts(second_client)
    identifier = f"{uuid.uuid4().hex}:acme.com"
    try:
        await attempts.record_failure(identifier)
        counter = await client.ttl(f"login:fail:{identifier}")
        assert 0 < counter <= 900
        assert await attempts.is_locked(identifier) is False

        for _ in range(4):
            await attempts.record_failure(identifier)

        assert await attempts.is_locked(identifier) is True
        # Another instance shares the verdict, and the lock carries the window.
        assert await other_instance.is_locked(identifier) is True
        lock_ttl = await client.ttl(f"login:lock:{identifier}")
        assert 0 < lock_ttl <= 900
        assert await client.get(f"login:fail:{identifier}") is None

        await attempts.clear(identifier)
        assert await attempts.is_locked(identifier) is False
        assert await other_instance.is_locked(identifier) is False
    finally:
        await client.delete(f"login:fail:{identifier}", f"login:lock:{identifier}")
        await attempts.aclose()
        await other_instance.aclose()
        await client.aclose()
        await second_client.aclose()
