"""Audit-log domain event, emission helper, and RQ handoff."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from app.events.bus import bus
from app.queue import enqueue_audit_log

log = structlog.get_logger(__name__)

AUDIT_LOG_EVENT = "audit_log"


@dataclass(frozen=True, slots=True)
class AuditLogEvent:
    """Immutable snapshot of an auditable action.

    Field names mirror the ``audit_logs`` table; ``occurred_at`` is captured at
    emission time and mapped to ``created_at`` by the worker so the audit row
    records when the action happened, not when the queue drained.
    """

    tenant_id: uuid.UUID
    action: str
    resource: str
    user_id: uuid.UUID | None = None
    resource_id: str | None = None
    details: dict[str, Any] | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


async def emit_audit_log(event: AuditLogEvent) -> None:
    """Publish an ``audit_log`` event; the handler enqueues it for async write."""
    await bus.emit(AUDIT_LOG_EVENT, event=event)


async def _handle_audit_log(sender: str, event: AuditLogEvent) -> None:
    # Fire-and-forget: offload the synchronous Redis/RQ enqueue to a worker
    # thread so it never blocks the event loop, and swallow failures — audit is
    # best-effort and must never fail the business request.
    try:
        await asyncio.to_thread(enqueue_audit_log, asdict(event))
    except Exception:
        log.warning(
            "audit_log_enqueue_failed",
            action=event.action,
            resource=event.resource,
            exc_info=True,
        )


_registered = False


def register_audit_log_handler() -> None:
    """Subscribe the audit handler to the bus exactly once (idempotent)."""
    global _registered
    if _registered:
        return
    bus.subscribe(AUDIT_LOG_EVENT, _handle_audit_log)
    _registered = True
