"""RQ wiring for background jobs (async audit-log persistence)."""

from __future__ import annotations

from typing import Any

from redis import Redis
from rq import Queue

from app.config import get_settings

AUDIT_LOG_QUEUE = "audit-log"
AUDIT_LOG_JOB = "app.workers.audit.write_audit_log"

_queue: Queue | None = None


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
