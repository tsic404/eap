"""Execution lease tests: one live holder, renewals, and takeover after expiry."""

from __future__ import annotations

import time
import uuid

from redis import Redis

from app.services.task_lease import LEASE_KEY, ExecutionLease


def _lease(client: Redis, outbox_id: str, **kwargs: float) -> ExecutionLease:
    return ExecutionLease(outbox_id, client=client, **kwargs)


def _key(outbox_id: str) -> str:
    return LEASE_KEY.format(outbox_id=outbox_id)


def _wait_gone(client: Redis, key: str, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while client.exists(key) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert client.exists(key) == 0


def test_second_holder_cannot_take_a_live_lease(redis_url: str) -> None:
    client = Redis.from_url(redis_url)
    outbox_id = f"lease-{uuid.uuid4().hex}"
    holder = _lease(client, outbox_id)
    duplicate = _lease(client, outbox_id)
    try:
        assert holder.acquire() is True
        assert duplicate.acquire() is False
        assert holder.renew() is True
    finally:
        holder.release()
        assert client.exists(_key(outbox_id)) == 0
        client.close()


def test_expired_lease_is_taken_over_once_and_the_old_holder_cannot_renew(
    redis_url: str,
) -> None:
    client = Redis.from_url(redis_url)
    outbox_id = f"lease-{uuid.uuid4().hex}"
    crashed = _lease(client, outbox_id, ttl_seconds=1)
    replay = _lease(client, outbox_id)
    try:
        assert crashed.acquire() is True
        _wait_gone(client, _key(outbox_id))

        assert replay.acquire() is True
        assert crashed.renew() is False
        # The dead holder's release must not drop the new holder's lease.
        crashed.release()
        assert client.exists(_key(outbox_id)) == 1
    finally:
        replay.release()
        client.close()


def test_heartbeat_keeps_a_long_execution_leased(redis_url: str) -> None:
    client = Redis.from_url(redis_url)
    outbox_id = f"lease-{uuid.uuid4().hex}"
    holder = _lease(client, outbox_id, ttl_seconds=1, renew_seconds=0.2)
    duplicate = _lease(client, outbox_id)
    try:
        assert holder.acquire() is True
        holder.start()
        # Longer than the TTL: only the renewals keep the lease alive.
        time.sleep(1.4)
        assert duplicate.acquire() is False
        holder.stop()
        assert duplicate.acquire() is True
    finally:
        duplicate.release()
        client.close()


def test_renewal_reports_a_lease_taken_over_by_another_worker(redis_url: str) -> None:
    client = Redis.from_url(redis_url)
    outbox_id = f"lease-{uuid.uuid4().hex}"
    holder = _lease(client, outbox_id, ttl_seconds=1, renew_seconds=0.1)
    try:
        assert holder.acquire() is True
        holder.start()
        client.delete(_key(outbox_id))  # what a takeover after expiry leaves

        deadline = time.monotonic() + 2
        while not holder.lost and time.monotonic() < deadline:
            time.sleep(0.02)
        assert holder.lost is True
    finally:
        holder.stop()
        client.close()


def test_retired_claim_key_does_not_block_the_lease(redis_url: str) -> None:
    """Rollout window: a leftover one-hour claim under the old name is inert."""
    client = Redis.from_url(redis_url)
    outbox_id = f"lease-{uuid.uuid4().hex}"
    legacy_key = f"task:exec:{outbox_id}"
    client.set(legacy_key, "1", ex=3600)
    holder = _lease(client, outbox_id)
    try:
        assert holder.acquire() is True
        # The old key is neither read nor deleted: only the new one leases.
        assert client.get(legacy_key) == b"1"
    finally:
        holder.release()
        client.delete(legacy_key)
        client.close()
