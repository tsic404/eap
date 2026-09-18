from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Conversation(Base):
    """A user's chat session with an agent; message content lives in Dify.

    The row stores the platform-stable id (for frontend URLs and user-scoped
    listing) and the Dify conversation id learned lazily from the first streamed
    reply, since Dify creates a conversation only when the first message is
    posted (``conversation_id`` left empty until then). ``agent_name`` is
    denormalized at creation for list display (the agent may later be renamed).
    """

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_name: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    dify_conversation_id: Mapped[str | None] = mapped_column(String(255))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        # User-scoped listing filters by (user_id, deleted_at) most often.
        Index("ix_conversations_user_deleted", "user_id", "deleted_at"),
        Index("ix_conversations_tenant_user", "tenant_id", "user_id"),
        # Same-tenant integrity: agent and user must belong to this
        # conversation's tenant. The composite keys are the sole referential
        # guards — single-column FKs to tenants/users/agent_registry would be
        # redundant with these and would not enforce tenant consistency.
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["agent_registry.tenant_id", "agent_registry.agent_id"],
            name="fk_conversations_tenant_agent_id_agent_registry",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_conversations_tenant_user_id_users",
        ),
        CheckConstraint("status IN ('active', 'archived')", name="ck_conversations_status"),
    )
