"""Local account password login (C-110 ~ C-112).

Judged in the contractual order: capability switch, client-IP login limit,
request fields, tenant, lockout, credentials. The credential step answers one
indistinguishable 401 for an unknown account, a wrong password and a disabled
account. The session it opens is the existing one — the access token still comes
only from the refresh channel (C-2/C-3), and the login method takes no part in
any authorization judgement.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.auth.login_lockout import LoginAttemptStore
from app.auth.password import hash_password, verify_password
from app.auth.tenants import resolve_tenant_by_email_domain
from app.config import Settings
from app.core.exceptions import AuthError
from app.errors import AppError
from app.models.user import User
from app.rate_limit import LOCAL_LOGIN_PATH, TokenBucket, bucket_key, login_rule

log = structlog.get_logger(__name__)

# Verified against when no account (or no password) was found, so the rejection
# costs the same as a wrong password and cannot be told apart by latency.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


@dataclass(frozen=True)
class LocalLoginResult:
    """A successful local login: the account and its forced-change state."""

    user: User
    must_change_password: bool


class LocalLoginService:
    """Judges a local login request and, on success, hands back the account."""

    def __init__(
        self,
        settings: Settings,
        *,
        limiter: TokenBucket,
        attempts: LoginAttemptStore,
    ) -> None:
        self._settings = settings
        self._limiter = limiter
        self._attempts = attempts

    async def authenticate(
        self,
        session: AsyncSession,
        *,
        email: Any,
        password: Any,
        client_ip: str,
    ) -> LocalLoginResult:
        """Authenticate the given credentials or raise the contractual error."""
        # ① Capability switch: disabled means no login path at all (C-110).
        if not self._settings.local_login_enabled:
            raise AuthError(404, "NOT_FOUND", "Not Found")

        # ② Client-IP login limit (C-10), the same bucket the OIDC start uses.
        await self._consume_login_budget(client_ip)

        # ③ Request fields.
        address, secret = self._require_credentials(email, password)

        # ④ Tenant matched by email domain, folded like the callback (C-23).
        tenant = await resolve_tenant_by_email_domain(session, address)

        # ⑤ Failure lockout: refuses without verifying the password.
        identifier = f"{tenant.id}:{address}"
        if await self._attempts.is_locked(identifier):
            raise AuthError(429, "LOGIN_LOCKED", "Too many failed login attempts")

        # ⑥ Credentials: unknown, wrong password and disabled are one answer.
        user = await self._find_account(session, tenant_id=tenant.id, email=address)
        stored = user.password_hash if user is not None and user.password_hash else _DUMMY_HASH
        authenticated = verify_password(secret, stored)
        if user is None or user.status != "active" or not authenticated:
            await self._attempts.record_failure(identifier)
            log.info(
                "local_login_rejected",
                resource="local_login",
                tenant_id=str(tenant.id),
                email=address,
                account_found=user is not None,
            )
            raise AuthError(401, "INVALID_CREDENTIALS", "Invalid email or password")

        await self._attempts.clear(identifier)
        return LocalLoginResult(user=user, must_change_password=user.must_change_password)

    async def _consume_login_budget(self, client_ip: str) -> None:
        """Charge one token of the login bucket, or answer 429 ``RATE_LIMITED``.

        The route owns this consumption instead of the rate-limit middleware so
        the capability switch above is judged first (C-111 ① before ②); the
        bucket key and rule are the middleware's, so both login paths share one
        count per client IP.
        """
        rule = login_rule(self._settings)
        if rule is None:
            return
        granted = await self._limiter.acquire(
            bucket_key(rule, client_ip, None), rule.capacity, rule.refill_per_second
        )
        if not granted:
            log.warning(
                "rate_limited",
                resource="rate_limit",
                resourceId=rule.name,
                client_ip=client_ip,
                path=LOCAL_LOGIN_PATH,
            )
            raise AuthError(429, "RATE_LIMITED", "Rate limit exceeded")

    @staticmethod
    def _require_credentials(email: Any, password: Any) -> tuple[str, str]:
        """Return the folded address and the password, or the 422 of step ③."""
        address = email.strip().casefold() if isinstance(email, str) else ""
        secret = password if isinstance(password, str) else ""
        missing = [
            name
            for name, present in (("email", bool(address)), ("password", bool(secret)))
            if not present
        ]
        if missing:
            raise AppError(
                422,
                "VALIDATION_ERROR",
                "Request validation failed",
                details=[
                    {"type": "missing", "loc": ["body", name], "msg": "Field required"}
                    for name in missing
                ],
            )
        return address, secret

    @staticmethod
    async def _find_account(
        session: AsyncSession, *, tenant_id: uuid.UUID, email: str
    ) -> User | None:
        """Load the tenant's account for ``email``; None when it is not exactly one.

        Addresses are one identifier regardless of case: the local part is folded
        for the lookup and for the failure counter alike, so a differently cased
        address neither reaches another account nor sidesteps the lockout. Two
        rows folding onto one address is contradictory data and reads as no
        account, rather than picking one the caller may not have meant.
        """
        users = (
            (
                await session.execute(
                    select(User)
                    .where(User.tenant_id == tenant_id, func.lower(User.email) == email)
                    .limit(2)
                    # Scalars only: skip the selectin loads (sessions, runs, tasks).
                    .options(noload("*"))
                )
            )
            .scalars()
            .all()
        )
        return users[0] if len(users) == 1 else None
