"""Access-token issuance and refresh-token material (hashing, entropy)."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings

# Access tokens are short-lived: the OIDC flow issues 15-minute tokens and the
# refresh endpoint reports this window back to the client as ``expiresIn``.
ACCESS_TOKEN_TTL_SECONDS = 900

# Refresh tokens are random 256-bit strings; only their SHA-256 digest is
# persisted, so a database leak does not expose usable tokens.
_REFRESH_TOKEN_BYTES = 64


def hash_token(raw: str) -> str:
    """Return the storage-side digest of a refresh token."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_refresh_token() -> str:
    """Return a new URL-safe random refresh token (the raw value sent to the client)."""
    return secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)


def issue_access_token(
    settings: Settings,
    *,
    sub: str,
    tenant_id: str,
    role: str,
    session_id: str,
    now: datetime | None = None,
) -> tuple[str, int]:
    """Sign an RS256 access token carrying ``sub``/``tenantId``/``role``/``jti``/``sid``.

    Returns ``(token, ttl_seconds)``. ``sid`` names the session the token belongs
    to — the refresh-token family — so logout can end it without the refresh
    credential, which C-3 confines to the refresh endpoint.
    """
    now = now or datetime.now(UTC)
    payload = {
        "sub": sub,
        "tenantId": tenant_id,
        "role": role,
        "jti": str(uuid.uuid4()),
        "sid": session_id,
        "iat": now,
        "exp": now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS),
    }
    token = jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")
    return token, ACCESS_TOKEN_TTL_SECONDS
