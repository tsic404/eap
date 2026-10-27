"""In-flight upload slots: process cap, per-user cap, waiting and release."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from app.errors import AppError
from app.uploads import UploadSlots


async def _enter(
    slots: UploadSlots,
    user_id: str,
    live: list[int],
    peak: list[int],
    release: asyncio.Event,
) -> None:
    """Hold one slot until ``release``, recording the peak concurrency."""
    async with slots.hold(user_id):
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        try:
            await release.wait()
        finally:
            live[0] -= 1


async def _until(reached: Callable[[], bool]) -> None:
    for _ in range(10_000):
        if reached():
            return
        await asyncio.sleep(0)
    raise AssertionError("the expected state was never reached")


@pytest.mark.asyncio
async def test_process_slots_cap_the_uploads_in_flight() -> None:
    slots = UploadSlots(max_slots=2, per_user_slots=3, wait_s=5.0)
    live = [0]
    peak = [0]
    release = asyncio.Event()
    tasks = [
        asyncio.create_task(_enter(slots, f"user-{index}", live, peak, release))
        for index in range(3)
    ]

    await _until(lambda: live[0] == 2)
    release.set()
    await asyncio.gather(*tasks)

    assert peak == [2]


@pytest.mark.asyncio
async def test_per_user_slots_cap_one_account_below_the_process_cap() -> None:
    slots = UploadSlots(max_slots=4, per_user_slots=2, wait_s=5.0)
    live = [0]
    peak = [0]
    release = asyncio.Event()
    tasks = [
        asyncio.create_task(_enter(slots, "same-user", live, peak, release))
        for _ in range(3)
    ]

    await _until(lambda: live[0] == 2)
    release.set()
    await asyncio.gather(*tasks)

    assert peak == [2]


@pytest.mark.asyncio
async def test_saturated_process_answers_429_and_the_slot_is_reusable() -> None:
    slots = UploadSlots(max_slots=1, per_user_slots=1, wait_s=0.05)
    occupied = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with slots.hold("holder"):
            occupied.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await occupied.wait()

    with pytest.raises(AppError) as refused:
        async with slots.hold("queued"):
            raise AssertionError("the queued upload must not enter")

    assert refused.value.status_code == 429
    assert refused.value.code == "RATE_LIMITED"

    release.set()
    await task

    async with slots.hold("queued"):
        pass


@pytest.mark.asyncio
async def test_slots_are_released_when_the_handler_body_raises() -> None:
    slots = UploadSlots(max_slots=1, per_user_slots=1, wait_s=0.05)

    with pytest.raises(RuntimeError, match="handler failed"):
        async with slots.hold("user-1"):
            raise RuntimeError("handler failed")

    async with slots.hold("user-1"):
        pass
