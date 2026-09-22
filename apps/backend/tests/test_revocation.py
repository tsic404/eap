"""Access-token revocation registry tests: marker TTL, skips, and outage policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from redis.exceptions import RedisError

from app.auth.revocation import (
    is_access_token_revoked,
    revoke_access_token,
    revoked_jti_key,
)
from app.auth.tokens import ACCESS_TOKEN_TTL_SECONDS
from tests.conftest import FakeRedis

# The registry never verifies signatures — that boundary is the middleware's —
# so any algorithm and key stand in for a real RS256 access token here.
_SIGNING_KEY = "revocation-test-signing-key-0123456789"


def _access_token(*, jti: object = "jti-1", ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "user-1",
            "jti": jti,
            "iat": now,
            "exp": now + timedelta(seconds=ttl_seconds),
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )


class _UnavailableRedis:
    """Client whose calls all fail, as during a Redis outage."""

    async def get(self, name: str) -> str | None:
        raise RedisError("connection refused")

    async def set(self, name: str, value: str, *, ex: int | None = None) -> None:
        raise RedisError("connection refused")


@pytest.mark.asyncio
async def test_revoked_jti_is_readable_for_the_tokens_remaining_lifetime() -> None:
    redis = FakeRedis()
    token = _access_token()

    assert await revoke_access_token(redis, token) is True  # type: ignore[arg-type]

    key = revoked_jti_key("jti-1")
    assert await redis.get(key) == "1"
    ttl = redis.recorded_ttl(key)
    # The marker must expire no later than the token it revokes, so the registry
    # self-cleans instead of growing without bound.
    assert ttl is not None
    assert 0 < ttl <= ACCESS_TOKEN_TTL_SECONDS

    assert await is_access_token_revoked(redis, "jti-1") is True  # type: ignore[arg-type]
    assert await is_access_token_revoked(redis, "jti-other") is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_revoke_skips_tokens_that_need_no_marker() -> None:
    redis = FakeRedis()

    # Unparseable, already expired, and jti-less tokens all leave the registry
    # untouched rather than raising into the logout path.
    assert await revoke_access_token(redis, "not-a-jwt") is False  # type: ignore[arg-type]
    assert (
        await revoke_access_token(redis, _access_token(ttl_seconds=-1))  # type: ignore[arg-type]
        is False
    )
    assert (
        await revoke_access_token(redis, _access_token(jti=None)) is False  # type: ignore[arg-type]
    )
    assert await revoke_access_token(None, _access_token()) is False

    assert await redis.get(revoked_jti_key("jti-1")) is None


@pytest.mark.asyncio
async def test_registry_fails_open_when_redis_is_unavailable() -> None:
    # Failing closed would turn a Redis outage into a platform-wide logout; the
    # missed revocation only lasts until the token's own exp.
    unavailable = _UnavailableRedis()
    assert await is_access_token_revoked(unavailable, "jti-1") is False  # type: ignore[arg-type]
    assert await revoke_access_token(unavailable, _access_token()) is False  # type: ignore[arg-type]

    # No registry (e.g. a process without Redis wired up) and no jti claim are
    # likewise not grounds to reject a request.
    assert await is_access_token_revoked(None, "jti-1") is False
    assert await is_access_token_revoked(FakeRedis(), None) is False  # type: ignore[arg-type]
