"""Authentication dependencies: resolve the authenticated user from identity.

The JWT middleware already verifies the bearer-token signature and stashes
``sub``/``tenantId``/``role``/``jti`` on ``request.state``. This dependency is
the *enforcement* half: it rejects requests with no resolved identity, a
revoked access token or an account that is gone or no longer active (C-13 ①-③),
then hands the loaded account to the endpoint.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.revocation import is_access_token_revoked
from app.core.exceptions import AuthError
from app.db import get_session
from app.models.user import User

# C-113: while an account must change its password, every request except a
# password change, a refresh and a logout is refused with this code.
PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"


async def get_authenticated_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Return the authenticated account, or raise ``401 UNAUTHORIZED``.

    This is the gate-free loader: the endpoints the forced-change rule exempts
    (the password change itself) depend on it, everything else goes through
    ``get_current_user``.
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise AuthError(401, "UNAUTHORIZED", "Not authenticated")
    if await is_access_token_revoked(
        getattr(request.app.state, "redis", None), getattr(request.state, "jti", None)
    ):
        raise AuthError(401, "TOKEN_REVOKED", "Access token revoked")
    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        raise AuthError(401, "UNAUTHORIZED", "Not authenticated") from None
    user = await session.get(User, user_uuid)
    if user is None or user.status != "active":
        raise AuthError(401, "UNAUTHORIZED", "Not authenticated")
    return user


async def get_current_user(
    user: Annotated[User, Depends(get_authenticated_user)],
) -> User:
    """Return the account of a business request: authenticated and changed (C-113)."""
    if user.must_change_password:
        raise AuthError(403, PASSWORD_CHANGE_REQUIRED, "Password change required")
    return user
