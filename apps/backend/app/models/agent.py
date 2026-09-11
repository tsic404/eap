from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
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
    from app.models.knowledge import KnowledgeBaseRegistry
    from app.models.run_log import RunLog
    from app.models.tenant import Tenant
    from app.models.tool import ToolRegistry
    from app.models.user import User


class AgentRegistry(Base):
    __tablename__ = "agent_registry"

    agent_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    dify_app_id: Mapped[str] = mapped_column(String(100), nullable=False)
    dify_api_key: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(50), default="chat", nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    visibility: Mapped[str] = mapped_column(String(50), default="department", nullable=False)
    icon: Mapped[str | None] = mapped_column(String(50), default="🤖")
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(255))
    model_name: Mapped[str | None] = mapped_column(String(255))
    model_provider: Mapped[str | None] = mapped_column(String(255))
    prompt: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    tenant: Mapped[Tenant] = relationship(back_populates="agent_registries")
    creator: Mapped[User | None] = relationship(
        back_populates="created_agents", foreign_keys=[created_by]
    )
    knowledge_bindings: Mapped[list[AgentKnowledgeBinding]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", lazy="selectin"
    )
    tool_bindings: Mapped[list[AgentToolBinding]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", lazy="selectin"
    )
    daily_stats: Mapped[list[AgentDailyStat]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", lazy="selectin"
    )
    run_logs: Mapped[list[RunLog]] = relationship(back_populates="agent", lazy="selectin")

    __table_args__ = (
        Index("ix_agent_registry_tenant_id", "tenant_id"),
        Index("ix_agent_registry_tenant_status", "tenant_id", "status"),
        CheckConstraint(
            "type IN ('chat', 'workflow', 'agent', 'data')", name="ck_agent_registry_type"
        ),
        CheckConstraint(
            "status IN ('draft', 'testing', 'published', 'offline')",
            name="ck_agent_registry_status",
        ),
        CheckConstraint("version >= 1", name="ck_agent_registry_version"),
    )


class AgentKnowledgeBinding(Base):
    __tablename__ = "agent_knowledge_bindings"

    agent_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("agent_registry.agent_id", ondelete="CASCADE"),
        primary_key=True,
    )
    kb_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("knowledge_base_registry.kb_id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    agent: Mapped[AgentRegistry] = relationship(back_populates="knowledge_bindings")
    knowledge: Mapped[KnowledgeBaseRegistry] = relationship(back_populates="agent_bindings")


class AgentToolBinding(Base):
    __tablename__ = "agent_tool_bindings"

    agent_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("agent_registry.agent_id", ondelete="CASCADE"),
        primary_key=True,
    )
    tool_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("tool_registry.tool_id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    agent: Mapped[AgentRegistry] = relationship(back_populates="tool_bindings")
    tool: Mapped[ToolRegistry] = relationship(back_populates="agent_bindings")


class AgentDailyStat(Base):
    __tablename__ = "agent_daily_stats"

    agent_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("agent_registry.agent_id", ondelete="CASCADE"), primary_key=True
    )
    stat_date: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_latency_ms: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    agent: Mapped[AgentRegistry] = relationship(back_populates="daily_stats")

    __table_args__ = (
        CheckConstraint(
            "calls >= 0 AND successes >= 0 AND failures >= 0 AND "
            "total_latency_ms >= 0 AND total_tokens >= 0",
            name="ck_agent_daily_stats_non_negative",
        ),
    )
