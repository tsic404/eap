"""Route worker jobs onto the API event loop when the consumer runs in-process.

``process_task`` spawns a fresh event loop per job, which is right for an
out-of-process ``rq worker``: it owns its process and therefore its engine. The
in-process executor shares this process's async engine, whose pooled
connections belong to the API loop, so it binds that loop here and job bodies
run there rather than opening a second loop over the same pool.
"""

from __future__ import annotations

import asyncio

_runtime_loop: asyncio.AbstractEventLoop | None = None


def bind_runtime_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Point subsequent jobs at ``loop``; ``None`` restores the per-job loop."""
    global _runtime_loop
    _runtime_loop = loop


def get_runtime_loop() -> asyncio.AbstractEventLoop | None:
    """The bound loop, or ``None`` when nothing consumes jobs in this process."""
    return _runtime_loop
