"""Observable in-flight pool state for the upload acceptance cases.

A case that must prove a request is *queued* on the pool cannot sleep and hope:
on a slow machine the request may still be inside identity or database work, so
the assertion holds even when the cap is wrong. Wrapping the pool counts the
requests that reached ``hold`` and the ones it admitted, which is the
synchronization point those cases wait on.

The second service process runs this same wrapper and exposes it on
``PROBE_PATH``; the parent drives it through the shipped app, not a stub.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from app.uploads import UploadSlots, _Lease

PROBE_PATH = "/__upload_pool"


@dataclass
class PoolProbe:
    """One service process's pool plus counts of what reached and holds it.

    ``entered`` is cumulative (every request that reached the pool), ``admitted``
    and ``waiting`` are current: a request sits in ``waiting`` from ``hold``
    entry until the pool lets it in. The counts follow the wrapper's own window,
    so a lease released early, by a client disconnect, stays counted until that
    request ends.

    ``hold`` hands out the wrapped pool's lease: the upload path releases it
    when the client goes away, so a stand-in that yielded anything else would
    turn that release into an error raised out of the disconnect watcher.
    """

    pool: UploadSlots
    entered: int = 0
    admitted: int = 0
    waiting: int = 0

    @asynccontextmanager
    async def hold(self, user_id: Any) -> AsyncIterator[_Lease]:
        self.entered += 1
        self.waiting += 1
        is_admitted = False
        try:
            async with self.pool.hold(user_id) as lease:
                is_admitted = True
                self.waiting -= 1
                self.admitted += 1
                try:
                    yield lease
                finally:
                    self.admitted -= 1
        finally:
            if not is_admitted:
                self.waiting -= 1

    def snapshot(self) -> dict[str, int]:
        return {"entered": self.entered, "admitted": self.admitted, "waiting": self.waiting}
