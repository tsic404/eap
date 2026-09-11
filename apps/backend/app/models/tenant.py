from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.agent import AgentRegistry
    from app.models.audit_log import AuditLog
    from app.models.knowledge import KnowledgeBaseRegistry
    from app.models.run_log import RunLog
    from app.models.task import Task
    from app.models.tool import ToolRegistry
    from app.models.user import User
    from app.models.user_memory import UserMemory


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    sso_domain: Mapped[str | None] = mapped_column(String(255))
    sso_provider: Mapped[str] = mapped_column(String(50), default="local", nullable=False)
    sso_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    quota_limit: Mapped[int] = mapped_column(Integer, default=10000, nullable=False)
    quota_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    users: Mapped[list[User]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", lazy="selectin"
    )
    agent_registries: Mapped[list[AgentRegistry]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", lazy="selectin"
    )
    knowledge_bases: Mapped[list[KnowledgeBaseRegistry]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", lazy="selectin"
    )
    tools: Mapped[list[ToolRegistry]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", lazy="selectin"
    )
    tasks: Mapped[list[Task]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", lazy="selectin"
    )
    user_memories: Mapped[list[UserMemory]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", lazy="selectin"
    )
    run_logs: Mapped[list[RunLog]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", lazy="selectin"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint(
            "quota_limit >= 0 AND quota_used >= 0", name="ck_tenants_quota_non_negative"
        ),
    )
