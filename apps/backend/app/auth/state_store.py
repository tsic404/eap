"""Redis-backed OIDC state store (architecture §33.1).

State carries the PKCE ``code_verifier`` and the OIDC ``nonce`` between the
login redirect and the callback. It is written with ``SET NX EX 600`` and read
with ``GETDEL`` so a value is atomically consumed exactly once — replaying an
already-used ``state`` yields nothing and is rejected as ``INVALID_STATE``.
"""

from __future__ import annotations

import json
from typing import Any, cast

from redis.asyncio import Redis


class OidcStateStore:
    """Single-use state store with a hard 10-minute TTL."""

    TTL_SECONDS = 600
    _KEY_PREFIX = "oidc:state:"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _key(self, state: str) -> str:
        return f"{self._KEY_PREFIX}{state}"

    async def put(self, state: str, payload: dict[str, Any]) -> bool:
        """Store ``payload`` under ``state``; False if the key already exists."""
        value = json.dumps(payload)
        return bool(await self._redis.set(self._key(state), value, nx=True, ex=self.TTL_SECONDS))

    async def consume(self, state: str) -> dict[str, Any] | None:
        """Atomically read-and-delete ``state``; None if absent or already used."""
        raw = await self._redis.getdel(self._key(state))
        if raw is None:
            return None
        return cast(dict[str, Any], json.loads(raw))
