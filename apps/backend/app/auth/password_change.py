"""Setting a local account's password (C-113 / C-115).

Two callers share this: an account changing its own password (forced first-login
change included) and a platform administrator resetting one. Both store the slow
hash, end every refresh-token family the account holds and decide the
forced-change flag — the difference is only whether the caller proves the
password it replaces first.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api_body import required_text
from app.auth.password import format_error, hash_password, verify_password
from app.auth.refresh import RefreshService
from app.core.exceptions import AuthError
from app.errors import invalid_field, validation_error
from app.models.user import User

# Revocation reasons for the audit trail on ``refresh_tokens`` (C-113).
PASSWORD_CHANGED_REASON = "password_change"
PASSWORD_RESET_REASON = "password_reset"


async def set_local_password(
    session: AsyncSession,
    user: User,
    *,
    password: str,
    must_change: bool,
    reason: str,
    refresh_service: RefreshService,
) -> None:
    """Store ``password`` for ``user`` and end its refresh-token families.

    Access tokens already issued stay valid until their own expiry — the 900 s
    window C-29 defines — so the caller commits and the account logs in again
    with the new password.
    """
    user.password_hash = hash_password(password)
    user.must_change_password = must_change
    await refresh_service.revoke_all_for_user(session, user.id, reason=reason)


async def change_own_password(
    session: AsyncSession,
    user: User,
    *,
    body: dict[str, Any],
    refresh_service: RefreshService,
) -> None:
    """Judge and apply the account's own password change (C-113).

    The replaced password must be proven, and the new one must satisfy the
    format rule and differ from the old one; either failure is answered without
    anything being stored.
    """
    fields = required_text(body, "currentPassword", "newPassword")
    current = fields["currentPassword"]
    new = fields["newPassword"]

    reason = format_error(new)
    if reason is not None:
        raise validation_error(invalid_field("newPassword", reason))

    stored = user.password_hash or ""
    if not verify_password(current, stored):
        raise AuthError(401, "INVALID_CREDENTIALS", "Invalid password")
    if verify_password(new, stored):
        raise validation_error(invalid_field("newPassword", "Password is unchanged"))

    await set_local_password(
        session,
        user,
        password=new,
        must_change=False,
        reason=PASSWORD_CHANGED_REASON,
        refresh_service=refresh_service,
    )
    await session.commit()
