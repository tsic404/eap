"""RQ wiring for background jobs (audit-log persistence, tool approval dispatch)."""

from __future__ import annotations

from typing import Any

from redis import Redis
from rq import Queue

from app.config import get_settings

AUDIT_LOG_QUEUE = "audit-log"
AUDIT_LOG_JOB = "app.workers.audit.write_audit_log"

TOOL_APPROVAL_QUEUE = "tool-approval"
TOOL_APPROVAL_JOB = "app.workers.approval.dispatch_approval"

_queue: Queue | None = None
_approval_queue: Queue | None = None


def audit_log_queue() -> Queue:
    """Return the process-wide ``audit-log`` RQ queue, created lazily once."""
    global _queue
    if _queue is None:
        _queue = Queue(AUDIT_LOG_QUEUE, connection=Redis.from_url(get_settings().redis_url))
    return _queue


def enqueue_audit_log(payload: dict[str, Any], queue: Queue | None = None) -> None:
    """Enqueue an audit-log write for the RQ ``audit-log`` worker.

    ``queue`` is injectable for tests; production callers use the shared queue.
    """
    (queue or audit_log_queue()).enqueue(AUDIT_LOG_JOB, payload)


def tool_approval_queue() -> Queue:
    """Return the process-wide ``tool-approval`` RQ queue, created lazily once."""
    global _approval_queue
    if _approval_queue is None:
        _approval_queue = Queue(
            TOOL_APPROVAL_QUEUE, connection=Redis.from_url(get_settings().redis_url)
        )
    return _approval_queue


def enqueue_tool_approval(payload: dict[str, Any], queue: Queue | None = None) -> None:
    """Enqueue a tool-approval dispatch for the RQ ``tool-approval`` worker.

    ``queue`` is injectable for tests; production callers use the shared queue.
    """
    (queue or tool_approval_queue()).enqueue(TOOL_APPROVAL_JOB, payload)
