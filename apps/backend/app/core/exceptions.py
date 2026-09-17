"""Shared domain exception types.

Kept apart from ``app.errors`` (HTTP response envelopes) — these carry the
information callers need to decide how to react, and are mapped onto the
platform error envelope at the router boundary.
"""

from __future__ import annotations


class DifyApiError(Exception):
    """Raised when the Dify Service API fails or its circuit breaker refuses a call.

    ``status_code`` mirrors the upstream HTTP status, or ``503`` when the local
    circuit breaker is open and the request is rejected before reaching Dify.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        # Dify's machine-readable error code (present on upstream ``error`` events).
        self.code = code
        super().__init__(message)


class AuthError(Exception):
    """Authentication/authorization failure mapped onto the error envelope.

    Unlike ``HTTPException``, ``code`` is a stable, machine-readable auth code
    (e.g. ``INVALID_STATE``, ``TOKEN_EXPIRED``, ``TOKEN_REUSE_DETECTED``) rather
    than the generic per-status label, so clients can branch on the exact
    failure without parsing message text.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)
