from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.agent import AgentRegistry
    from app.models.refresh_token import RefreshToken
    from app.models.run_log import RunLog
    from app.models.task import Task
    from app.models.tenant import Tenant
    from app.models.user_memory import UserMemory


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    sso_sub: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    department: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50), default="employee", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    avatar_text: Mapped[str | None] = mapped_column(String(10))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    tenant: Mapped[Tenant] = relationship(back_populates="users")
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    created_agents: Mapped[list[AgentRegistry]] = relationship(
        back_populates="creator", foreign_keys="AgentRegistry.created_by", lazy="selectin"
    )
    created_tasks: Mapped[list[Task]] = relationship(
        back_populates="creator", foreign_keys="Task.creator_id", lazy="selectin"
    )
    assigned_tasks: Mapped[list[Task]] = relationship(
        back_populates="assignee", foreign_keys="Task.assignee_id", lazy="selectin"
    )
    user_memories: Mapped[list[UserMemory]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    run_logs: Mapped[list[RunLog]] = relationship(back_populates="user", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("tenant_id", "sso_sub", name="uq_users_tenant_sso_sub"),
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index("ix_users_tenant_id", "tenant_id"),
        CheckConstraint(
            "role IN ('platform_admin', 'agent_admin', 'knowledge_admin', 'auditor', 'employee')",
            name="ck_users_role",
        ),
    )
