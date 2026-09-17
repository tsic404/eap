"""Refresh-token rotation with atomic claim and family revocation (§34.1).

Rotation safety rules:

* A refresh token is claimed with ``SELECT … FOR UPDATE`` inside a single
  transaction, so two concurrent requests presenting the same token cannot both
  win — the loser observes the row already revoked.
* A freshly rotated token replayed within the 30-second grace window is treated
  as a concurrent double-submit (``409 TOKEN_REFRESH_IN_PROGRESS``), not a
  breach, so a retried client keeps its session.
* The same replay after the window is ``401 TOKEN_REUSE_DETECTED`` and revokes
  the token's entire family — a leaked token cannot keep minting sessions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import (
    generate_refresh_token,
    hash_token,
    issue_access_token,
)
from app.config import Settings
from app.core.exceptions import AuthError
from app.models.refresh_token import RefreshToken
from app.models.user import User

# A rotation is considered "in progress" (retryable) for this long after the
# winning claim; beyond it a replay is a security event.
GRACE_PERIOD_SECONDS = 30


@dataclass(frozen=True)
class TokenPair:
    """A minted access/refresh token pair returned to the auth flow."""

    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_in: int


class RefreshService:
    """Issues and rotates refresh tokens, revoking families on breach."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._refresh_ttl = timedelta(seconds=settings.refresh_token_ttl_seconds)

    async def issue(
        self,
        session: AsyncSession,
        user: User,
        *,
        now: datetime | None = None,
    ) -> TokenPair:
        """Start a new token family for ``user`` (login / re-authentication)."""
        now = now or datetime.now(UTC)
        raw = generate_refresh_token()
        session.add(
            RefreshToken(
                user_id=user.id,
                family_id=uuid.uuid4(),
                token_hash=hash_token(raw),
                expires_at=now + self._refresh_ttl,
            )
        )
        await session.flush()
        await session.commit()
        return self._pair(
            sub=str(user.id),
            tenant_id=str(user.tenant_id),
            role=user.role,
            raw_refresh_token=raw,
            now=now,
        )

    async def rotate(
        self,
        session: AsyncSession,
        raw_token: str,
        *,
        now: datetime | None = None,
    ) -> TokenPair:
        """Atomically claim ``raw_token`` and mint its successor in the family."""
        now = now or datetime.now(UTC)
        token = await session.scalar(
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_token(raw_token))
            .with_for_update()
        )
        if token is None:
            # Unknown or never-issued token: indistinguishable from expired to
            # the client, and it leaks nothing about which tokens exist.
            raise AuthError(401, "TOKEN_EXPIRED", "Refresh token expired")

        if token.revoked_at is not None:
            if (
                token.revoked_reason == "rotated"
                and (now - token.revoked_at).total_seconds() <= GRACE_PERIOD_SECONDS
            ):
                raise AuthError(409, "TOKEN_REFRESH_IN_PROGRESS", "Refresh already in progress")
            # A replay beyond the window is a breach: revoke the whole family
            # and persist it even though the caller gets an error back.
            await self._revoke_family(session, token.family_id, reason="reuse", now=now)
            await session.commit()
            raise AuthError(401, "TOKEN_REUSE_DETECTED", "Refresh token reuse detected")

        if token.expires_at <= now:
            raise AuthError(401, "TOKEN_EXPIRED", "Refresh token expired")

        # Fetch only the claims needed to mint the access token — a full ORM
        # load would selectin-eager-load every User relationship.
        identity = (
            await session.execute(
                select(User.id, User.tenant_id, User.role).where(User.id == token.user_id)
            )
        ).one_or_none()
        if identity is None:
            raise AuthError(401, "TOKEN_EXPIRED", "Refresh token expired")

        token.revoked_at = now
        token.revoked_reason = "rotated"
        successor = generate_refresh_token()
        session.add(
            RefreshToken(
                user_id=token.user_id,
                family_id=token.family_id,
                token_hash=hash_token(successor),
                expires_at=now + self._refresh_ttl,
            )
        )
        await session.flush()
        await session.commit()
        return self._pair(
            sub=str(identity.id),
            tenant_id=str(identity.tenant_id),
            role=identity.role,
            raw_refresh_token=successor,
            now=now,
        )

    async def revoke(
        self,
        session: AsyncSession,
        raw_token: str,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> None:
        """Revoke the family containing ``raw_token`` (no-op if unknown)."""
        token = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
        )
        if token is None:
            return
        await self.revoke_family(session, token.family_id, reason=reason, now=now)

    async def revoke_family(
        self,
        session: AsyncSession,
        family_id: uuid.UUID,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> None:
        """Revoke every still-active token in a family (logout / breach)."""
        now = now or datetime.now(UTC)
        await self._revoke_family(session, family_id, reason=reason, now=now)
        await session.commit()

    async def _revoke_family(
        self,
        session: AsyncSession,
        family_id: uuid.UUID,
        *,
        reason: str,
        now: datetime,
    ) -> None:
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoked_reason=reason)
        )

    def _pair(
        self,
        *,
        sub: str,
        tenant_id: str,
        role: str,
        raw_refresh_token: str,
        now: datetime,
    ) -> TokenPair:
        access_token, expires_in = issue_access_token(
            self._settings,
            sub=sub,
            tenant_id=tenant_id,
            role=role,
            now=now,
        )
        return TokenPair(
            access_token=access_token,
            expires_in=expires_in,
            refresh_token=raw_refresh_token,
            refresh_expires_in=self._settings.refresh_token_ttl_seconds,
        )
