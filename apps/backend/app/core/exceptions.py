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
