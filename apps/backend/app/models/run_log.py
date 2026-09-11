from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.agent import AgentRegistry
    from app.models.tenant import Tenant
    from app.models.user import User


class RunLog(Base):
    __tablename__ = "run_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("agent_registry.agent_id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    agent_name: Mapped[str | None] = mapped_column(String(255))
    user_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(50))
    input: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(255))
    token_usage: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    knowledge_hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dify_message_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    tenant: Mapped[Tenant] = relationship(back_populates="run_logs")
    agent: Mapped[AgentRegistry] = relationship(
        back_populates="run_logs", foreign_keys=[agent_id]
    )
    user: Mapped[User] = relationship(back_populates="run_logs", foreign_keys=[user_id])
    steps: Mapped[list[TraceStep]] = relationship(
        back_populates="log", cascade="all, delete-orphan", lazy="selectin"
    )
    citations: Mapped[list[TraceCitation]] = relationship(
        back_populates="log", cascade="all, delete-orphan", lazy="selectin"
    )
    tool_calls: Mapped[list[TraceToolCall]] = relationship(
        back_populates="log", cascade="all, delete-orphan", lazy="selectin"
    )
    __table_args__ = (
        Index("ix_run_logs_tenant_created_at", "tenant_id", "created_at"),
        # Same-tenant integrity: agent and user must belong to this run log's
        # tenant (guards against cross-tenant references).
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["agent_registry.tenant_id", "agent_registry.agent_id"],
            name="fk_run_logs_tenant_agent_id_agent_registry",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_run_logs_tenant_user_id_users",
        ),
        CheckConstraint(
            "status IN ('success', 'failed', 'running', 'blocked')", name="ck_run_logs_status"
        ),
        CheckConstraint(
            "token_usage >= 0 AND latency_ms >= 0 AND tool_call_count >= 0 "
            "AND knowledge_hit_count >= 0",
            name="ck_run_logs_metrics_non_negative",
        ),
    )


class TraceStep(Base):
    __tablename__ = "trace_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("run_logs.trace_id"), nullable=False
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(String(50))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)

    log: Mapped[RunLog] = relationship(back_populates="steps")

    __table_args__ = (CheckConstraint("step_order >= 0", name="ck_trace_steps_step_order"),)


class TraceCitation(Base):
    __tablename__ = "trace_citations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("run_logs.trace_id"), nullable=False
    )
    source_name: Mapped[str | None] = mapped_column(String(255))
    kb_name: Mapped[str | None] = mapped_column(String(255))
    excerpt: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)

    log: Mapped[RunLog] = relationship(back_populates="citations")


class TraceToolCall(Base):
    __tablename__ = "trace_tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("run_logs.trace_id"), nullable=False
    )
    tool_name: Mapped[str | None] = mapped_column(String(255))
    tool_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(String(50))
    permission_mode: Mapped[str | None] = mapped_column(String(20))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    request_summary: Mapped[str | None] = mapped_column(Text)
    response_summary: Mapped[str | None] = mapped_column(Text)

    log: Mapped[RunLog] = relationship(back_populates="tool_calls")
