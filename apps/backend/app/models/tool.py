from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.agent import AgentToolBinding
    from app.models.tenant import Tenant


class ToolRegistry(Base):
    __tablename__ = "tool_registry"

    tool_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(50), default="http", nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(2048))
    method: Mapped[str | None] = mapped_column(String(10), default="POST")
    risk_level: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    permission_mode: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    auth_type: Mapped[str | None] = mapped_column(String(50), default="none")
    auth_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=10000, nullable=False)
    retry_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    circuit_breaker: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    tenant: Mapped[Tenant | None] = relationship(back_populates="tools")
    agent_bindings: Mapped[list[AgentToolBinding]] = relationship(
        back_populates="tool", lazy="selectin"
    )
    debug_cases: Mapped[list[ToolDebugCase]] = relationship(
        back_populates="tool", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_tool_registry_tenant_id", "tenant_id"),
        # Same-tenant integrity for tenant-scoped tools (skipped for global
        # tools where tenant_id IS NULL): created_by must belong to the tool's
        # tenant.
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name="fk_tool_registry_tenant_created_by_users",
        ),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')", name="ck_tool_registry_risk_level"
        ),
        CheckConstraint(
            "permission_mode IN ('auto', 'confirm', 'disabled')",
            name="ck_tool_registry_permission_mode",
        ),
        CheckConstraint("timeout_ms >= 0", name="ck_tool_registry_timeout_ms"),
    )


class ToolDebugCase(Base):
    __tablename__ = "tool_debug_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tool_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("tool_registry.tool_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    request_body: Mapped[str | None] = mapped_column(Text)
    response_body: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tool: Mapped[ToolRegistry] = relationship(back_populates="debug_cases")

    __table_args__ = (
        CheckConstraint(
            "latency_ms >= 0 AND (status_code IS NULL OR "
            "(status_code >= 100 AND status_code <= 599))",
            name="ck_tool_debug_cases_ranges",
        ),
    )
