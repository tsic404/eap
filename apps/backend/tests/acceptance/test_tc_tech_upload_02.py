"""Executable acceptance case for TC-tech-upload-02 (AC-T23).

The in-flight pool belongs to a service process, not to the deployment. Both
sides of the boundary are real OS processes here: two uvicorn subprocesses boot
the shipped app against the same PostgreSQL and Redis, and a Dify stub in the
test process holds their uploads at the one mocked hop.

It also judges that the in-flight refusal is a second, independent 429: it
happens while the per-minute upload token bucket (C-10) still has budget, and the
two refusals carry the same code.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.acceptance.upload_pool import (
    GATE_SETTLE_S,
    SMALL_FILE,
    ServiceProcess,
    UploadGate,
    dify_stub,
    owned_connections,
    post_attachment,
    service_process,
    timed,
    until,
    until_async,
)

TC_ID = "TC-tech-upload-02"

# C-124's queue window; the case only needs the in-flight refusal to have
# happened, and the shipped default is what it observes.
_SLOT_WAIT_S = 15.0


async def _upload(tasks: list[asyncio.Task], *, timeout: float = 10.0) -> list:
    """Await the requests; a pool that never admitted them fails the case."""
    try:
        return list(await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout))
    except TimeoutError:
        raise AssertionError("uploads never completed: the pool did not admit them") from None


async def _is_queued(service: ServiceProcess, *, entered: int, admitted: int) -> bool:
    """Whether this process's pool has one request entered-but-not-admitted."""
    state = await service.pool()
    return state["entered"] == entered and state["admitted"] == admitted and state["waiting"] == 1


@pytest.mark.asyncio
async def test_one_account_holds_two_uploads_in_each_of_two_processes(api) -> None:
    """The same account sits in two processes at once: four uploads in flight."""
    async with owned_connections(api) as world:
        tenant, (member,) = await world.seed_tenant(api)
        headers = api.auth(member, tenant)
        gate = UploadGate()
        async with dify_stub(gate) as dify_url:
            async with (
                service_process(api, dify_url=dify_url) as first,
                service_process(api, dify_url=dify_url) as second,
            ):
                gate.hold()
                uploads = [
                    asyncio.create_task(post_attachment(service.client, headers))
                    for service in (first, second)
                    for _ in range(2)
                ]
                await until(lambda: gate.entries >= 4, what="four uploads in flight")
                for service in (first, second):
                    assert await service.pool() == {"entered": 2, "admitted": 2, "waiting": 0}

                # Neither process admitted a third upload for the account: it is
                # queued on that process's own pool, not on the other one.
                extra = asyncio.create_task(post_attachment(first.client, headers))
                await until_async(
                    lambda: _is_queued(first, entered=3, admitted=2),
                    what="the third upload waits on one process's pool",
                )
                await asyncio.sleep(GATE_SETTLE_S)
                assert gate.entries == 4, (
                    "a third upload for the account reached the upstream boundary "
                    "while its two slots were held"
                )
                assert gate.peak == 4

                gate.release()
                responses = await _upload([*uploads, extra])

    assert [response.status_code for response in responses] == [201] * 5
    assert gate.peak == 4


@pytest.mark.asyncio
async def test_two_processes_carry_four_uploads_each_so_no_global_cap_applies(api) -> None:
    """Four in flight per process, eight at once: the capacity is per process."""
    async with owned_connections(api) as world:
        tenant, members = await world.seed_tenant(api, roles=["knowledge_admin"] * 5)
        gate = UploadGate()
        async with dify_stub(gate) as dify_url:
            async with (
                service_process(api, dify_url=dify_url) as first,
                service_process(api, dify_url=dify_url) as second,
            ):
                gate.hold()
                uploads = [
                    asyncio.create_task(post_attachment(service.client, api.auth(member, tenant)))
                    for service, granted in ((first, members[:2]), (second, members[2:4]))
                    for member in granted
                    for _ in range(2)
                ]
                await until(lambda: gate.entries >= 8, what="eight uploads in flight")
                for service in (first, second):
                    assert await service.pool() == {"entered": 4, "admitted": 4, "waiting": 0}

                # A ninth upload queued on the first process: its four slots are
                # taken, and no deployment-wide limit is what admits or refuses it.
                extra = asyncio.create_task(
                    post_attachment(first.client, api.auth(members[4], tenant))
                )
                await until_async(
                    lambda: _is_queued(first, entered=5, admitted=4),
                    what="the ninth upload waits on the first process's pool",
                )
                await asyncio.sleep(GATE_SETTLE_S)
                assert gate.entries == 8, (
                    "a ninth upload reached the upstream boundary while the process pool held four"
                )
                assert gate.peak == 8

                gate.release()
                responses = await _upload([*uploads, extra])

    assert [response.status_code for response in responses] == [201] * 9
    assert gate.peak == 8


@pytest.mark.asyncio
async def test_in_flight_refusal_owes_nothing_to_the_per_minute_upload_bucket(api) -> None:
    """A 429 arrives inside the bucket's budget, and both refusals share the code."""
    async with owned_connections(api) as world:
        tenant, (member,) = await world.seed_tenant(api)
        headers = api.auth(member, tenant)
        gate = UploadGate()
        async with dify_stub(gate) as dify_url:
            async with service_process(api, dify_url=dify_url, rate_limited=True) as service:
                gate.hold()

                # Budget spent in this 60-second window, as the token bucket counts it.
                requests_in_window = 0
                held = [
                    asyncio.create_task(post_attachment(service.client, headers)) for _ in range(2)
                ]
                await until(lambda: gate.entries >= 2, what="both slots of the account are taken")
                requests_in_window += 2

                refusal, elapsed = await asyncio.wait_for(
                    timed(post_attachment(service.client, headers)),
                    timeout=_SLOT_WAIT_S + 10.0,
                )
                requests_in_window += 1

                assert refusal.status_code == 429, refusal.text
                inflight_code = refusal.json()["error"]["code"]
                assert inflight_code == "RATE_LIMITED"
                # The answer took the whole queue window: it came from the pool,
                # not from an early verdict somewhere in the request path.
                assert elapsed >= _SLOT_WAIT_S - 0.1
                # The per-minute bucket cannot explain this refusal: the window
                # has spent three of its ten tokens.
                assert requests_in_window < 10

                gate.release()
                assert [response.status_code for response in await _upload(held)] == [201, 201]

                # Drain the bucket and read the refusal the bucket itself produces.
                bucket_refusal = None
                for _ in range(20):
                    attempt = await post_attachment(service.client, headers, SMALL_FILE)
                    requests_in_window += 1
                    if attempt.status_code == 429:
                        bucket_refusal = attempt
                        break
                    assert attempt.status_code == 201, attempt.text

                assert bucket_refusal is not None, "the upload token bucket never refused"
                assert bucket_refusal.json()["error"]["code"] == inflight_code
