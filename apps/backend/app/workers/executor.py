"""In-process task executor: drain the RQ task queues inside the API process.

The deployment contract fixes the resident container set, so the consumer of
the task queues cannot be a container of its own: it runs as a thread in the
backend. RQ's ``SimpleWorker`` executes a job in this thread instead of forking
a work horse. Dispatch jobs carry a retry budget (``app.queue``), so a job whose
worker died is requeued by the maintenance pass — delivery stays at-least-once,
and the worker's execution lease and status guards absorb the repeat.

The work loop is written here rather than taken from ``Worker.work()``, which
stops only when a SIGINT/SIGTERM handler raises inside its blocking dequeue —
impossible from a thread. RQ documents ``bootstrap``/``execute_job`` for exactly
this: a different loop over the same bookkeeping. The SIGALRM job timeout is
switched off for the same reason (per-tool timeouts already bound each call).
"""

from __future__ import annotations

import asyncio
import threading
import time

import structlog
from redis import Redis
from rq import Queue, SimpleWorker
from rq.exceptions import DequeueTimeout
from rq.timeouts import BaseDeathPenalty

from app.config import Settings
from app.queue import KNOWLEDGE_INDEX_QUEUE, MEMORY_EXTRACTION_QUEUE, TOOL_APPROVAL_QUEUE
from app.workers.bridge import bind_runtime_loop

log = structlog.get_logger(__name__)

# Every queue that carries task dispatch (app.queue.QUEUE_NAME_MAP minus the
# audit-log queue, whose job persists audit events rather than running tasks).
TASK_QUEUES: tuple[str, ...] = (
    TOOL_APPROVAL_QUEUE,
    KNOWLEDGE_INDEX_QUEUE,
    MEMORY_EXTRACTION_QUEUE,
)

# Bound on the blocking dequeue, so a stop request is honoured within a second
# without turning the idle loop into a busy poll.
_DEQUEUE_TIMEOUT_SECONDS = 1
_STOP_TIMEOUT_SECONDS = 5
_RESTART_DELAY_SECONDS = 5
_CONNECT_TIMEOUT_SECONDS = 1
# The worker registry entry only feeds monitoring; RQ's own loop refreshes it
# between jobs, and an idle loop must not write two keys every second.
_HEARTBEAT_INTERVAL_SECONDS = 30
# RQ ships its own console logger; app logs are structlog JSON. Errors only,
# so a failed job is visible without interleaving RQ's INFO chatter.
_WORKER_LOG_LEVEL = "WARNING"


class _NoJobTimeout(BaseDeathPenalty):
    """Job timeout without SIGALRM, which only exists in the main thread."""

    def setup_death_penalty(self) -> None:
        """No-op: the alarm cannot be armed outside the main thread."""

    def cancel_death_penalty(self) -> None:
        """No-op: nothing was armed."""


class _ThreadWorker(SimpleWorker):
    """``SimpleWorker`` adapted to a background thread."""

    death_penalty_class = _NoJobTimeout


class TaskExecutor:
    """Owns the executor thread and its lifecycle."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        """Start consuming; jobs are dispatched onto the running (API) loop."""
        if self._thread is not None:
            return
        bind_runtime_loop(asyncio.get_running_loop())
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="task-executor", daemon=True)
        self._thread.start()
        log.info("task_executor_started", queues=list(TASK_QUEUES))

    async def stop(self) -> None:
        """Stop consuming and wait, bounded, for the thread to leave its loop.

        The join runs off the event loop: job bodies are dispatched onto this
        very loop, so joining on it would deadlock until the timeout and leave
        the job no way to finish. A thread that outlives the timeout keeps its
        handle and the loop binding, so a job still in flight can complete
        instead of failing on a torn-down runtime.
        """
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        await asyncio.to_thread(thread.join, _STOP_TIMEOUT_SECONDS)
        if thread.is_alive():
            log.warning("task_executor_stop_timeout", timeout=_STOP_TIMEOUT_SECONDS)
            return
        self._thread = None
        # Detached from the loop: a job replayed afterwards must fall back to
        # its own event loop instead of scheduling onto a stopped one.
        bind_runtime_loop(None)
        log.info("task_executor_stopped")

    def _run(self) -> None:
        """Consume until stopped, rebuilding the worker after a Redis outage."""
        connection = Redis.from_url(
            self._settings.redis_url, socket_connect_timeout=_CONNECT_TIMEOUT_SECONDS
        )
        try:
            while not self._stop.is_set():
                try:
                    self._consume(connection)
                except Exception:
                    # Never let the thread die: a Redis outage or a raising job
                    # is retried, and the lifespan's stop flag still ends it.
                    log.warning("task_executor_crashed", exc_info=True)
                if not self._stop.is_set():
                    self._stop.wait(_RESTART_DELAY_SECONDS)
        finally:
            connection.close()

    def _consume(self, connection: Redis) -> None:
        """Run the work loop on a fresh worker; returns when asked to stop."""
        queues = [Queue(name, connection=connection) for name in TASK_QUEUES]
        worker = _ThreadWorker(queues, connection=connection)
        worker.bootstrap(logging_level=_WORKER_LOG_LEVEL)
        next_heartbeat = 0.0
        try:
            while not self._stop.is_set():
                if time.monotonic() >= next_heartbeat:
                    worker.heartbeat()
                    next_heartbeat = time.monotonic() + _HEARTBEAT_INTERVAL_SECONDS
                if worker.should_run_maintenance_tasks:
                    # Retires workers that died mid-job and requeues their jobs.
                    worker.run_maintenance_tasks()  # type: ignore[no-untyped-call]
                try:
                    dequeued = Queue.dequeue_any(
                        queues,
                        _DEQUEUE_TIMEOUT_SECONDS,
                        connection=connection,
                        job_class=worker.job_class,
                        serializer=worker.serializer,
                        death_penalty_class=worker.death_penalty_class,
                    )
                except DequeueTimeout:
                    continue
                if dequeued is None:
                    continue
                job, queue = dequeued
                job.redis_server_version = worker.get_redis_server_version()  # type: ignore[no-untyped-call]
                worker.execute_job(job, queue)
        finally:
            worker.teardown()  # type: ignore[no-untyped-call]
