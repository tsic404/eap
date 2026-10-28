"""``TaskExecutor`` lifecycle: shutdown must not require the event loop it feeds."""

from __future__ import annotations

import asyncio
import importlib
import threading
import time

import pytest

from app.config import Settings
from app.workers.executor import TaskExecutor

_EXECUTOR_MODULE = importlib.import_module("app.workers.executor")


@pytest.mark.asyncio
async def test_stop_does_not_block_the_running_loop() -> None:
    """The join runs off-loop, so a job dispatched onto that loop can finish."""
    executor = TaskExecutor(Settings(_env_file=None))
    thread = threading.Thread(target=lambda: time.sleep(1.0), daemon=True)
    thread.start()
    executor._thread = thread  # the thread under test; no queue work needed

    ticks = 0

    async def _tick() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker = asyncio.create_task(_tick())
    try:
        await executor.stop()
    finally:
        ticker.cancel()

    assert ticks >= 3
    assert executor._thread is None


@pytest.mark.asyncio
async def test_stop_keeps_the_handle_when_the_thread_outlives_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A job still in flight keeps its worker handle and loop binding."""
    monkeypatch.setattr(_EXECUTOR_MODULE, "_STOP_TIMEOUT_SECONDS", 0.05)
    executor = TaskExecutor(Settings(_env_file=None))
    thread = threading.Thread(target=lambda: time.sleep(0.5), daemon=True)
    thread.start()
    executor._thread = thread

    await executor.stop()

    assert executor._thread is thread
    thread.join(timeout=5)
