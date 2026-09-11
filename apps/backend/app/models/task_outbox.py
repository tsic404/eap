from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TaskOutboxEvent(Base):
    """Transactional outbox row for task status transitions.

    Written in the same transaction as the task state change and published to
    the worker queue by a reconciler, so a queue-enqueue failure can never leave
    a task permanently stuck (see architecture §33.3).
    """

    __tablename__ = "task_outbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # No FK to tasks.id by design: outbox rows must outlive their task so the
    # reconciler can still requeue after the task row is cleaned up (§33.3).
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # queue name
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # triggering status
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_task_outbox_delivered_created", "delivered_at", "created_at"),)
