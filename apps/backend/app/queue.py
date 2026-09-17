"""RQ wiring for background jobs (audit-log persistence, tool approval dispatch).

Task execution (§32.7) enqueues ``process_task`` onto the queue named by the
task type (see ``QUEUE_NAME_MAP``). Dispatch is idempotent only through the
outbox *claim* (``claim_outbox_row``) — RQ does NOT deduplicate by ``job_id``
(rq>=2.0), so callers must enqueue and then claim (or claim then enqueue) with
the worker's atomic execution claim as the backstop against duplicates.
"""

from __future__ import annotations

from typing import Any

from redis import Redis
from rq import Queue

from app.config import get_settings

AUDIT_LOG_QUEUE = "audit-log"
AUDIT_LOG_JOB = "app.workers.audit.write_audit_log"

TOOL_APPROVAL_QUEUE = "tool-approval"
TOOL_APPROVAL_JOB = "app.workers.approval.dispatch_approval"

KNOWLEDGE_INDEX_QUEUE = "knowledge-index-status"
MEMORY_EXTRACTION_QUEUE = "memory-extraction"

PROCESS_TASK_JOB = "app.workers.process_task.process_task"

# Task ``type`` -> RQ queue name (architecture §32.7.3). Unknown types fall back
# to the audit-log queue so a mis-typed task still lands somewhere observable.
QUEUE_NAME_MAP: dict[str, str] = {
    "tool_approval": TOOL_APPROVAL_QUEUE,
    "knowledge_index": KNOWLEDGE_INDEX_QUEUE,
    "memory_extraction": MEMORY_EXTRACTION_QUEUE,
    "audit_log": AUDIT_LOG_QUEUE,
}

_queue: Queue | None = None
_approval_queue: Queue | None = None
_task_queues: dict[str, Queue] = {}


def map_queue_name(task_type: str) -> str:
    """Resolve a task ``type`` to its RQ queue name."""
    return QUEUE_NAME_MAP.get(task_type, AUDIT_LOG_QUEUE)


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


def _task_queue(queue_name: str) -> Queue:
    """Return a cached RQ queue for ``queue_name`` (one Redis connection each)."""
    queue = _task_queues.get(queue_name)
    if queue is None:
        queue = Queue(queue_name, connection=Redis.from_url(get_settings().redis_url))
        _task_queues[queue_name] = queue
    return queue


def enqueue_process_task(
    task_id: str,
    outbox_id: str,
    queue_name: str,
    payload: dict[str, Any],
    queue: Queue | None = None,
) -> None:
    """Enqueue ``process_task`` for an approved/retried task.

    ``payload`` is passed as one positional argument (never ``**payload``) so a
    task payload key can never collide with RQ's reserved ``job_id``/``args``
    kwargs. ``job_id`` is the outbox event id for observability only — RQ does
    NOT deduplicate by ``job_id`` (rq>=2.0), so callers MUST atomically claim
    the outbox row (``claim_outbox_row``) *after a successful enqueue*;
    claim-before-enqueue would create a permanent-loss window. ``queue`` is
    injectable for tests.
    """
    (queue or _task_queue(queue_name)).enqueue(
        PROCESS_TASK_JOB,
        task_id,
        outbox_id,
        payload,
        job_id=outbox_id,
    )
