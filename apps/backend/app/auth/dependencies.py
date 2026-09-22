"""Authentication dependencies: resolve the authenticated user from identity.

The JWT middleware already verifies the bearer-token signature and stashes
``sub``/``tenantId``/``role``/``jti`` on ``request.state``. This dependency is
the *enforcement* half: it rejects requests with no resolved identity or a
revoked access token, then loads the matching, existing user from the database.
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


async def get_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Return the authenticated user or raise ``401 UNAUTHORIZED``."""
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
    if user is None:
        raise AuthError(401, "UNAUTHORIZED", "Not authenticated")
    return user
