"""Pydantic schemas for the tool module (architecture doc §32.5.2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

_TOOL_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"

# Hosts that expose cloud-instance metadata; a tool endpoint must never point at
# them (SSRF: registering such an endpoint lets a tool read the instance IAM
# credentials). Kept minimal by design — the review only required http/https +
# metadata-host rejection, not a full private-range allowlist.
_SSRF_BLOCKED_HOSTS = frozenset(
    {
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.goog",
    }
)


class ToolType(StrEnum):
    http = "http"
    database = "database"
    rpa = "rpa"
    webhook = "webhook"
    mcp = "mcp"


class RiskLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class PermissionMode(StrEnum):
    auto = "auto"
    confirm = "confirm"
    disabled = "disabled"


class ToolStatus(StrEnum):
    active = "active"
    disabled = "disabled"
    archived = "archived"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class AuthType(StrEnum):
    none = "none"
    api_key = "api_key"
    bearer = "bearer"
    basic = "basic"
    oauth2 = "oauth2"


class RetryPolicy(BaseModel):
    """Retry configuration: exponential backoff (``base_delay_ms * 2**attempt``)."""

    max_retries: int = Field(default=2, ge=0, le=5)
    base_delay_ms: int = Field(default=1000, ge=0)


class _EndpointValidated(BaseModel):
    """Shared SSRF guard for the ``endpoint`` field."""

    @field_validator("endpoint", check_fields=False)
    @classmethod
    def _validate_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("endpoint must use http or https")
        if not parsed.hostname:
            raise ValueError("endpoint must include a host")
        if parsed.hostname.lower() in _SSRF_BLOCKED_HOSTS:
            raise ValueError("endpoint host is not allowed")
        return value


class CreateToolDto(_EndpointValidated):
    """Registration payload for a new external-API tool."""

    name: str = Field(min_length=1, max_length=100)
    tool_id: str = Field(min_length=1, max_length=100, pattern=_TOOL_ID_PATTERN)
    type: ToolType = ToolType.http
    description: str | None = Field(default=None, max_length=500)
    risk_level: RiskLevel = RiskLevel.medium
    permission_mode: PermissionMode = PermissionMode.auto
    endpoint: str = Field(min_length=1, max_length=2048)
    method: HttpMethod = HttpMethod.POST
    auth_type: AuthType = AuthType.none
    auth_config: dict[str, Any] | None = None
    timeout_ms: int = Field(default=10000, ge=1)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    circuit_breaker: dict[str, Any] | None = None


class UpdateToolDto(_EndpointValidated):
    """Partial update payload; only provided fields are applied."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    type: ToolType | None = None
    risk_level: RiskLevel | None = None
    permission_mode: PermissionMode | None = None
    endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    method: HttpMethod | None = None
    auth_type: AuthType | None = None
    auth_config: dict[str, Any] | None = None
    timeout_ms: int | None = Field(default=None, ge=1)
    retry_policy: RetryPolicy | None = None
    circuit_breaker: dict[str, Any] | None = None
    status: ToolStatus | None = None


class DebugToolDto(BaseModel):
    """Online-debug payload: a test parameter set, optionally saved as a case."""

    params: dict[str, Any]
    save_as_test_case: bool = False
    name: str | None = Field(default=None, max_length=255)


class ToolRead(BaseModel):
    """Read shape for a registered tool."""

    model_config = ConfigDict(from_attributes=True)

    tool_id: str
    tenant_id: uuid.UUID | None
    name: str
    description: str | None
    type: str
    endpoint: str | None
    method: str | None
    risk_level: str
    permission_mode: str
    auth_type: str | None
    auth_config: dict[str, Any] | None
    timeout_ms: int
    retry_policy: dict[str, Any] | None
    circuit_breaker: dict[str, Any] | None
    status: str
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
