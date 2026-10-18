from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
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

# Account provenance (C-114): a local account carries the password credential a
# platform admin set, an SSO account is created by the enterprise login. The
# value is fixed when the row is created; no path may change it.
ACCOUNT_SOURCE_LOCAL = "local"
ACCOUNT_SOURCE_SSO = "sso"
# Local accounts keep their subject in a reserved namespace, so an identity
# provider subject can never address one (nor the reverse): the callback's
# find-or-create lookup by subject cannot reach a local account.
LOCAL_SUBJECT_PREFIX = "local:"


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
    account_source: Mapped[str] = mapped_column(
        String(16), default=ACCOUNT_SOURCE_SSO, server_default=text("'sso'"), nullable=False
    )
    # Local-account credentials (ADR-007): SSO accounts keep a NULL hash and
    # never use the local path; the flag is reported on local login (C-111).
    password_hash: Mapped[str | None] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
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
        back_populates="user",
        foreign_keys="UserMemory.user_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    run_logs: Mapped[list[RunLog]] = relationship(
        back_populates="user", foreign_keys="RunLog.user_id", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "sso_sub", name="uq_users_tenant_sso_sub"),
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        # One address is one account per tenant whatever its case (C-103/C-114):
        # the service folds the address when it looks for a conflict, so the
        # database folds it too and two concurrent creates cannot both land.
        # The case-sensitive key above still backs lookups by the stored text.
        Index(
            "uq_users_tenant_email_lower",
            "tenant_id",
            text("lower(email)"),
            unique=True,
        ),
        # Anchor for composite FKs: enables child tables to reference a user
        # scoped to the same tenant via (tenant_id, user_id).
        UniqueConstraint("tenant_id", "id", name="uq_users_tenant_id_id"),
        Index("ix_users_tenant_id", "tenant_id"),
        CheckConstraint(
            "role IN ('platform_admin', 'agent_admin', 'knowledge_admin', 'auditor', 'employee')",
            name="ck_users_role",
        ),
        CheckConstraint(
            "account_source IN ('local', 'sso')",
            name="ck_users_account_source",
        ),
        # Provenance and subject namespace must agree, so a source change cannot
        # hide behind an existing subject (C-114).
        CheckConstraint(
            "(account_source = 'local' AND sso_sub LIKE 'local:%')"
            " OR (account_source = 'sso' AND sso_sub NOT LIKE 'local:%')",
            name="ck_users_account_source_subject",
        ),
    )
