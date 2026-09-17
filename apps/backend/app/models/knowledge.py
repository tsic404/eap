from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.agent import AgentKnowledgeBinding
    from app.models.tenant import Tenant


class KnowledgeBaseRegistry(Base):
    __tablename__ = "knowledge_base_registry"

    kb_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    dify_dataset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    dify_api_key: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(50), default="business", nullable=False)
    owner: Mapped[str | None] = mapped_column(String(255))
    authorized_scope: Mapped[str | None] = mapped_column(String(255))
    indexing_status: Mapped[str | None] = mapped_column(String(50), default="ready")
    doc_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    tenant: Mapped[Tenant] = relationship(back_populates="knowledge_bases")
    agent_bindings: Mapped[list[AgentKnowledgeBinding]] = relationship(
        back_populates="knowledge", lazy="selectin"
    )
    documents: Mapped[list[KnowledgeDocument]] = relationship(
        back_populates="knowledge_base",
        lazy="raise",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_knowledge_base_registry_tenant_id", "tenant_id"),
        CheckConstraint(
            "indexing_status IN ('ready', 'indexing', 'failed')",
            name="ck_knowledge_base_registry_indexing_status",
        ),
        CheckConstraint(
            "doc_count >= 0 AND chunk_count >= 0",
            name="ck_knowledge_base_registry_counts_non_negative",
        ),
    )


class KnowledgeDocument(Base):
    """Per-document indexing state, tracked locally so terminal events fire once.

    Documents themselves live in Dify; this table records the last observed
    indexing status so ``document.indexed``/``document.index_failed`` are only
    emitted on a status *transition*, never on every poll.
    """

    __tablename__ = "knowledge_documents"

    kb_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("knowledge_base_registry.kb_id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="indexing", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    knowledge_base: Mapped[KnowledgeBaseRegistry] = relationship(back_populates="documents")

    __table_args__ = (
        # No separate ix on kb_id: the composite PK's leftmost prefix already
        # serves kb_id-scoped lookups.
        CheckConstraint(
            "status IN ('indexing', 'completed', 'failed')",
            name="ck_knowledge_documents_status",
        ),
    )
