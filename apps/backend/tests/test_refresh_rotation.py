"""Refresh-token rotation contract tests (architecture §34.1).

These exercise the atomic-claim, grace-window, reuse-detection, and
family-revocation rules against a real PostgreSQL instance (the backend runs on
Postgres; ``SELECT … FOR UPDATE`` row locking is the concurrency primitive).

The test database is the same one the CI workflow provisions; set
``TEST_DATABASE_URL`` to point elsewhere for local runs.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.refresh import GRACE_PERIOD_SECONDS, RefreshService
from app.auth.tokens import generate_refresh_token, hash_token
from app.config import Settings
from app.core.exceptions import AuthError
from app.models.refresh_token import RefreshToken
from app.models.tenant import Tenant
from app.models.user import User

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
_PUBLIC_PEM = _KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
).decode()


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        jwt_private_key=_PRIVATE_PEM,
        jwt_public_key=_PUBLIC_PEM,
        refresh_token_ttl_seconds=2592000,
    )


async def _create_user(session: AsyncSession) -> User:
    tenant = Tenant(name="Test Tenant", slug=f"test-{uuid.uuid4()}")
    session.add(tenant)
    await session.flush()
    user = User(
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4()}",
        email=f"user-{uuid.uuid4()}@example.com",
        name="Test User",
    )
    session.add(user)
    await session.commit()
    return user


@pytest.mark.asyncio
async def test_issue_starts_new_family(session_factory) -> None:  # type: ignore[no-untyped-def]
    service = RefreshService(_settings())
    async with session_factory() as session:
        user = await _create_user(session)
        pair = await service.issue(session, user)

    assert pair.expires_in == 900
    assert pair.refresh_expires_in == 2592000

    async with session_factory() as session:
        token = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(pair.refresh_token))
        )
        assert token is not None
        assert token.family_id is not None
        assert token.revoked_at is None


@pytest.mark.asyncio
async def test_rotate_revokes_old_and_mints_successor(session_factory) -> None:  # type: ignore[no-untyped-def]
    service = RefreshService(_settings())
    async with session_factory() as session:
        user = await _create_user(session)
        pair = await service.issue(session, user)
    old_hash = hash_token(pair.refresh_token)

    async with session_factory() as session:
        rotated = await service.rotate(session, pair.refresh_token)

    async with session_factory() as session:
        old = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == old_hash)
        )
        new = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(rotated.refresh_token))
        )
        assert old is not None and old.revoked_reason == "rotated"
        assert new is not None and new.revoked_at is None
        # Successor inherits the family so a breach can be traced up the chain.
        assert new.family_id == old.family_id


@pytest.mark.asyncio
async def test_unknown_token_returns_expired(session_factory) -> None:  # type: ignore[no-untyped-def]
    service = RefreshService(_settings())
    async with session_factory() as session:
        await _create_user(session)
        with pytest.raises(AuthError) as exc:
            await service.rotate(session, "never-issued")
    assert exc.value.status_code == 401
    assert exc.value.code == "TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_expired_token_returns_expired(session_factory) -> None:  # type: ignore[no-untyped-def]
    service = RefreshService(_settings())
    raw = generate_refresh_token()
    async with session_factory() as session:
        user = await _create_user(session)
        session.add(
            RefreshToken(
                user_id=user.id,
                family_id=uuid.uuid4(),
                token_hash=hash_token(raw),
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        await session.commit()

        with pytest.raises(AuthError) as exc:
            await service.rotate(session, raw)
    assert exc.value.status_code == 401
    assert exc.value.code == "TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_concurrent_rotation_single_winner(session_factory) -> None:  # type: ignore[no-untyped-def]
    service = RefreshService(_settings())
    async with session_factory() as session:
        user = await _create_user(session)
        pair = await service.issue(session, user)
    raw = pair.refresh_token

    async def attempt() -> object:
        async with session_factory() as session:
            return await service.rotate(session, raw)

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
    winners = [r for r in results if not isinstance(r, BaseException)]
    errors = [r for r in results if isinstance(r, AuthError)]
    assert len(winners) == 1
    assert len(errors) == 1
    assert errors[0].status_code == 409  # type: ignore[union-attr]
    assert errors[0].code == "TOKEN_REFRESH_IN_PROGRESS"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_reuse_beyond_grace_revokes_family(session_factory) -> None:  # type: ignore[no-untyped-def]
    service = RefreshService(_settings())
    async with session_factory() as session:
        user = await _create_user(session)
        pair = await service.issue(session, user)
    old_hash = hash_token(pair.refresh_token)

    async with session_factory() as session:
        successor = await service.rotate(session, pair.refresh_token)
    successor_hash = hash_token(successor.refresh_token)

    # Backdate the old token's rotation past the grace window to simulate a
    # replay arriving long after the legitimate refresh completed.
    async with session_factory() as session:
        old = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == old_hash)
        )
        assert old is not None
        old.revoked_at = datetime.now(UTC) - timedelta(seconds=GRACE_PERIOD_SECONDS + 10)
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(AuthError) as exc:
            await service.rotate(session, pair.refresh_token)
    assert exc.value.status_code == 401
    assert exc.value.code == "TOKEN_REUSE_DETECTED"

    # The whole family is revoked, including the freshly minted successor.
    async with session_factory() as session:
        successor_row = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == successor_hash)
        )
        assert successor_row is not None
        assert successor_row.revoked_at is not None
        assert successor_row.revoked_reason == "reuse"
