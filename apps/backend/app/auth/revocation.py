"""Access-token revocation registry (Redis).

An access token stays valid until its ``exp`` — logout cannot un-sign it — so
logout records the token's ``jti`` in Redis and protected requests consult that
registry before trusting the identity the JWT middleware decoded. The marker's
TTL is the token's remaining lifetime, so the registry never outlives the token
it revokes and needs no cleanup job.
"""

from __future__ import annotations

from datetime import UTC, datetime

import jwt
import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

log = structlog.get_logger(__name__)

_KEY_PREFIX = "revoked:jti:"


def revoked_jti_key(jti: str) -> str:
    """Return the Redis key holding the revocation marker for ``jti``."""
    return f"{_KEY_PREFIX}{jti}"


def _unverified_claims(token: str) -> dict[str, object] | None:
    """Parse a bearer token's claims without validating signature or times.

    Revocation is written for the caller's own token, so a forged ``jti`` only
    marks a token that does not exist; the signature boundary stays in the
    middleware and ``get_current_user``. Times are left unvalidated so the
    caller can decide that a token already past ``exp`` needs no marker.
    """
    try:
        return jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
                "verify_aud": False,
            },
        )
    except jwt.PyJWTError:
        return None


def _remaining_ttl_seconds(claims: dict[str, object]) -> int | None:
    """Seconds left before ``exp``; None when absent or already past."""
    exp = claims.get("exp")
    if not isinstance(exp, int | float):
        return None
    remaining = int(exp - datetime.now(UTC).timestamp())
    return remaining if remaining > 0 else None


async def is_access_token_revoked(redis: Redis | None, jti: str | None) -> bool:
    """Whether the access token identified by ``jti`` has been revoked.

    Fails open when the registry is unreachable or unset: refusing every request
    would turn a Redis outage into a platform-wide logout, while a missed marker
    only lasts until the token's own ``exp`` (≤15 minutes).
    """
    if redis is None or not jti:
        return False
    try:
        return await redis.get(revoked_jti_key(jti)) is not None
    except RedisError:
        log.warning("jti_revocation_check_failed", resource="jti_revocation", jti=jti)
        return False


async def revoke_access_token(redis: Redis | None, token: str) -> bool:
    """Mark ``token`` revoked for its remaining lifetime.

    Returns whether a marker was written. An unset registry, an unparseable
    token, a missing ``jti``, or a token already at ``exp`` all mean there is
    nothing to revoke — the caller's logout still completes.
    """
    if redis is None:
        return False
    claims = _unverified_claims(token)
    if claims is None:
        return False
    jti = claims.get("jti")
    ttl = _remaining_ttl_seconds(claims)
    if not isinstance(jti, str) or not jti or ttl is None:
        return False
    try:
        await redis.set(revoked_jti_key(jti), "1", ex=ttl)
    except RedisError:
        log.warning("jti_revocation_write_failed", resource="jti_revocation", jti=jti)
        return False
    return True
