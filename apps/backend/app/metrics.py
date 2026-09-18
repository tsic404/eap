"""Application-level Prometheus metrics (beyond HTTP instrumentation).

HTTP request count/latency histograms and the ``/metrics`` endpoint are provided
by ``prometheus_fastapi_instrumentator`` in ``app.main``. This module owns the
metrics that instrument the application's own dependencies: Dify Service-API
calls, the SQLAlchemy connection pool, and the RQ audit-log queue depth.
"""

from __future__ import annotations

from typing import Protocol, cast

from prometheus_client import Counter, Gauge, Histogram

# Dify Service-API call volume and latency. ``outcome`` is one of success,
# retry (an individual transient attempt), failure, or circuit_open.
DIFY_API_REQUESTS = Counter(
    "dify_api_requests_total",
    "Dify Service-API requests by upstream path and outcome.",
    ["path", "outcome"],
)
DIFY_API_DURATION = Histogram(
    "dify_api_request_duration_seconds",
    "Dify Service-API request latency.",
    ["path"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# SQLAlchemy async engine pool usage. Usage ratio = checkedout / size.
DB_POOL_SIZE = Gauge("db_pool_size", "Current SQLAlchemy connection-pool size.")
DB_POOL_CHECKEDOUT = Gauge("db_pool_checkedout", "Connections currently checked out of the pool.")
DB_POOL_OVERFLOW = Gauge("db_pool_overflow", "Connections in the pool overflow.")

# RQ queue depth for the audit-log background job. Reads Redis at scrape time;
# reports 0 while Redis is absent or the RQ worker has not been deployed.
RQ_AUDIT_LOG_DEPTH = Gauge("rq_audit_log_queue_depth", "RQ audit-log queue depth.")


class _PoolStats(Protocol):
    """The SQLAlchemy pool methods the gauge callbacks need."""

    def size(self) -> int: ...
    def checkedout(self) -> int: ...
    def overflow(self) -> int: ...


def register_db_pool_metrics() -> None:
    """Wire the gauges to read the async engine pool on every scrape."""
    from app.db import engine

    pool = cast(_PoolStats, engine.sync_engine.pool)
    DB_POOL_SIZE.set_function(pool.size)
    DB_POOL_CHECKEDOUT.set_function(pool.checkedout)
    DB_POOL_OVERFLOW.set_function(pool.overflow)


def register_rq_metrics(redis_url: str) -> None:
    """Wire the audit-log queue-depth gauge to Redis ``LLEN``.

    The RQ worker (audit-log persistence) is wired in ``app.queue``; until it
    enqueues jobs the queue key does not exist and the gauge reads 0.
    """
    import redis

    from app.queue import AUDIT_LOG_QUEUE

    # Sync client with bounded timeouts: the gauge callback runs inline during
    # a /metrics scrape on the event loop, so a hung Redis must not block all
    # requests indefinitely.
    client = redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
    )
    queue_key = f"rq:queue:{AUDIT_LOG_QUEUE}"

    def depth() -> float:
        try:
            return float(client.llen(queue_key))
        except redis.RedisError:
            return 0.0

    RQ_AUDIT_LOG_DEPTH.set_function(depth)
