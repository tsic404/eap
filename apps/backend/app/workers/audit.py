"""RQ worker job: persist an audit-log event into the ``audit_logs`` table.

Run with ``rq worker audit-log`` (see the ``audit-log`` queue in app.queue).
The job payload is the ``asdict`` of an ``AuditLogEvent``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from app.db import async_session_factory
from app.models.audit_log import AuditLog


def _audit_log_from_payload(payload: dict[str, Any]) -> AuditLog:
    """Map an ``AuditLogEvent`` dict onto the ``audit_logs`` row shape."""
    return AuditLog(
        tenant_id=uuid.UUID(str(payload["tenant_id"])),
        user_id=uuid.UUID(str(payload["user_id"])) if payload.get("user_id") else None,
        action=payload["action"],
        resource=payload["resource"],
        resource_id=payload.get("resource_id"),
        details=payload.get("details"),
        ip_address=payload.get("ip_address"),
        user_agent=payload.get("user_agent"),
        # ``occurred_at`` records when the action happened, not when the queue
        # drained; fall back to now only for payloads that predate the field.
        created_at=payload.get("occurred_at") or datetime.now(UTC),
    )


def write_audit_log(payload: dict[str, Any]) -> None:
    """RQ entrypoint: write one audit-log event (synchronous worker context)."""
    asyncio.run(_write(payload))


async def _write(payload: dict[str, Any]) -> None:
    event = _audit_log_from_payload(payload)
    async with async_session_factory() as session:
        session.add(event)
        await session.commit()
