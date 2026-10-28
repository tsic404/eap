"""Renewable execution lease for one task dispatch (C-76 at-least-once).

A dispatch can reach a worker more than once — a reconciler re-send, a rescue
re-send, an RQ replay. The lease turns those repeats into one execution: the
holder renews the key while the job runs, so a duplicate can only take over
after the holder stopped renewing (a crash) and the lease expired.

Short and renewed because the covered tool call has no bounded duration.
"""

from __future__ import annotations

import threading
import uuid

import structlog
from redis import Redis
from redis.exceptions import RedisError

from app.config import get_settings

log = structlog.get_logger(__name__)

# One renewal per third of the TTL (20 s at the default): two lost renewals
# still leave the lease alive, and a dead holder's lease is gone before the
# rescue scan's smallest grace period elapses.
LEASE_TTL_SECONDS = 60

# A different prefix from the retired fixed-window claim (``task:exec:``): its
# value was "1" with a one-hour TTL, so reusing the name would make a leftover
# key read as a live holder and stall the dispatch until it expired. Old keys
# are now inert; during a mixed-version rollout the two mechanisms can overlap,
# which at-least-once already permits.
LEASE_KEY = "task:lease:{outbox_id}"

# A straggling renewal may hold the stop; the release that follows is
# token-checked, so a renewal finishing after it is harmless.
_STOP_JOIN_SECONDS = 1.0

# Both scripts are compare-and-set: a plain GET followed by EXPIRE/DEL can act
# on a lease another worker took over between the two commands, so the check
# and the write must be one step in the store.
_RENEW_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

_redis_client: Redis | None = None


def _open_redis() -> Redis:
    """Return the process-wide client (one pool, like the rescue scan's)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(get_settings().redis_url)
    return _redis_client


class ExecutionLease:
    """One worker's claim on a dispatch record, renewed while the job runs."""

    def __init__(
        self,
        outbox_id: str,
        client: Redis | None = None,
        *,
        ttl_seconds: int = LEASE_TTL_SECONDS,
        renew_seconds: float | None = None,
    ) -> None:
        self._key = LEASE_KEY.format(outbox_id=outbox_id)
        self._token = uuid.uuid4().hex
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._renew_seconds = ttl_seconds / 3 if renew_seconds is None else renew_seconds
        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None
        # Set when a renewal found the key gone: another delivery owns the
        # dispatch now. The worker reads it to drop an in-flight call instead
        # of finishing it and writing an outcome on top of the new holder's.
        self.lost = False

    def _redis(self) -> Redis:
        if self._client is None:
            self._client = _open_redis()
        return self._client

    def acquire(self) -> bool:
        """Take the lease when no live holder has it; True when this worker won."""
        return bool(self._redis().set(self._key, self._token, nx=True, ex=self._ttl_seconds))

    def renew(self) -> bool:
        """Extend the lease this worker holds; False once it no longer holds it."""
        renewed = self._redis().eval(_RENEW_SCRIPT, 1, self._key, self._token, self._ttl_seconds)
        return bool(renewed)

    def release(self) -> None:
        """Drop the lease so a later delivery does not wait for the expiry."""
        self._redis().eval(_RELEASE_SCRIPT, 1, self._key, self._token)

    def start(self) -> None:
        """Renew in the background until :meth:`stop`."""
        if self._heartbeat is not None:
            return
        self._heartbeat = threading.Thread(target=self._keepalive, name="task-lease", daemon=True)
        self._heartbeat.start()

    def stop(self) -> None:
        """Stop renewing, then release the lease.

        Runs off the event loop (the caller awaits ``asyncio.to_thread``); the
        join is bounded so a renewal stuck on an unreachable store cannot hold
        the job for a full renewal interval. A store failure here only costs
        the TTL wait: the job's own outcome is already committed, so it must
        not turn into a failed job.
        """
        self._stop.set()
        heartbeat = self._heartbeat
        if heartbeat is not None:
            heartbeat.join(timeout=_STOP_JOIN_SECONDS)
            self._heartbeat = None
        try:
            self.release()
        except RedisError:
            log.warning("task_lease_release_failed", key=self._key, exc_info=True)

    def _keepalive(self) -> None:
        """Renew until stopped, tolerating a store blip but not a lost lease.

        A transient error leaves the key in place (the TTL still runs), so the
        next tick may succeed; a renewal that finds the key gone or owned by
        another token means a takeover happened and nothing is left to renew.
        """
        while not self._stop.wait(self._renew_seconds):
            try:
                renewed = self.renew()
            except RedisError:
                log.warning("task_lease_renew_failed", key=self._key, exc_info=True)
                continue
            if not renewed:
                self.lost = True
                log.warning("task_lease_lost", key=self._key)
                return
