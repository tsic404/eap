"""Executable acceptance case for TC-tech-upload-01 (AC-T22).

The case judges the upload in-flight pool end to end: one app object carries one
service process's pool (``app.state.upload_slots``), the Dify upload boundary is
the only mocked hop, and every upload is a real request through the full ASGI
stack (identity, tenant, role, size and format gates included).

Requests run on connections of their own (``upload_pool.owned_connections``):
the fixture binds every request to one connection inside one transaction, and two
requests cannot be in flight on one connection.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from tests.acceptance.pool_probe import PoolProbe
from tests.acceptance.upload_pool import (
    GATE_SETTLE_S,
    UNSUPPORTED_FILE,
    UploadGate,
    fresh_kb_id,
    oversized_file,
    owned_connections,
    post_attachment,
    post_document,
    raw_upload,
    timed,
    until,
    upload_limit_headers,
)

TC_ID = "TC-tech-upload-01"

# C-124 waits 15 seconds for a slot before answering; the case gives the whole
# queue window room to elapse plus a margin for the rejected upload's handling.
_SLOT_WAIT_S = 15.0


async def _upload(tasks: list[asyncio.Task], *, timeout: float = 10.0) -> list:
    """Await the requests; a pool that never admitted them fails the case."""
    try:
        return list(await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout))
    except TimeoutError:
        raise AssertionError("uploads never completed: the pool did not admit them") from None


@pytest.mark.asyncio
async def test_one_account_peaks_at_two_in_flight_and_the_third_runs_after_a_slot_frees(
    api,
) -> None:
    """Same account, three concurrent uploads: peak two, the third waits."""
    async with owned_connections(api) as world:
        world.bind(api.app)
        tenant, (member,) = await world.seed_tenant(api)
        headers = api.auth(member, tenant)
        gate = UploadGate()
        gate.install(api.console)
        gate.hold()
        probe = PoolProbe(api.app.state.upload_slots)
        api.app.state.upload_slots = probe

        uploads = [asyncio.create_task(post_attachment(api.client, headers)) for _ in range(3)]
        # Judge the queued third against the boundary itself: the two admitted
        # uploads hold leases before they parse their bodies and arrive there.
        await until(lambda: gate.entries >= 2, what="two uploads reached the upstream boundary")
        await until(lambda: probe.entered == 3, what="all three uploads reached the pool")
        await asyncio.sleep(GATE_SETTLE_S)
        assert gate.entries == 2, (
            "a third upload reached the upstream boundary while the account held two slots"
        )
        assert probe.admitted == 2, "the third upload was admitted although the account was full"
        assert probe.waiting == 1, "the third upload did not queue on the pool"

        gate.release()
        responses = await _upload(uploads)

    assert [response.status_code for response in responses] == [201, 201, 201]
    assert gate.peak == 2


@pytest.mark.asyncio
async def test_five_accounts_share_one_process_pool_of_four_across_both_upload_routes(
    api,
) -> None:
    """Five accounts, mixed routes: four in flight, the fifth waits on the pool."""
    async with owned_connections(api) as world:
        world.bind(api.app)
        kb_id = fresh_kb_id(api.worker_index)
        tenant, members = await world.seed_tenant(
            api, roles=["knowledge_admin"] * 5, kb_ids=(kb_id,)
        )
        gate = UploadGate()
        gate.install(api.console)
        gate.hold()
        probe = PoolProbe(api.app.state.upload_slots)
        api.app.state.upload_slots = probe

        uploads = [
            *(
                asyncio.create_task(post_attachment(api.client, api.auth(member, tenant)))
                for member in members[:3]
            ),
            *(
                asyncio.create_task(
                    post_document(api.client, api.auth(member, tenant), kb_id=kb_id)
                )
                for member in members[3:]
            ),
        ]
        # All five cleared identity, tenant and role before the pool; the
        # boundary counts four, and the fifth is queued behind them.
        await until(lambda: gate.entries >= 4, what="four uploads reached the upstream boundary")
        await until(lambda: probe.entered == 5, what="all five uploads reached the pool")
        await asyncio.sleep(GATE_SETTLE_S)
        assert gate.entries == 4, (
            "a fifth upload reached the upstream boundary while the process pool held four"
        )
        assert gate.peak == 4
        assert probe.admitted == 4, "the fifth upload was admitted although every slot was held"
        assert probe.waiting == 1, "the fifth upload did not queue on the pool"

        gate.release()
        responses = await _upload(uploads)

    assert [response.status_code for response in responses] == [201] * 5
    assert gate.peak == 4


@pytest.mark.asyncio
async def test_a_waited_out_queue_answers_429_and_that_verdict_outranks_size_and_format(
    api,
) -> None:
    """A full pool answers 429 after the wait, ahead of the body's own verdicts.

    While the pool is full, an anonymous and an under-privileged caller are still
    answered at once: their gates resolve before the body and before any slot.
    """
    async with owned_connections(api) as world:
        world.bind(api.app)
        kb_id = fresh_kb_id(api.worker_index)
        tenant, members = await world.seed_tenant(
            api, roles=["knowledge_admin"] * 5 + ["employee"], kb_ids=(kb_id,)
        )
        employee = members[-1]
        gate = UploadGate()
        gate.install(api.console)
        gate.hold()

        # Two accounts hold both of their slots each, so the process pool (four)
        # is full and no per-account limit is what the queued uploads hit.
        holding = [
            *(
                asyncio.create_task(post_attachment(api.client, api.auth(member, tenant)))
                for member in members[:2]
                for _ in range(2)
            )
        ]
        await until(lambda: gate.entries >= 4, what="the process pool is full")

        queued = [
            timed(post_attachment(api.client, api.auth(members[2], tenant))),
            timed(post_attachment(api.client, api.auth(members[3], tenant), oversized_file())),
            timed(post_attachment(api.client, api.auth(members[4], tenant), UNSUPPORTED_FILE)),
            timed(post_document(api.client, {}, kb_id=kb_id)),
            timed(post_document(api.client, api.auth(employee, tenant), kb_id=kb_id)),
        ]
        refused, oversized, unsupported, anonymous, forbidden = await asyncio.wait_for(
            asyncio.gather(*queued), timeout=_SLOT_WAIT_S + 20.0
        )

        gate.release()
        releases = await _upload(holding)

    for response, elapsed in (refused, oversized, unsupported):
        assert response.status_code == 429, response.text
        assert response.json()["error"]["code"] == "RATE_LIMITED"
        assert upload_limit_headers(response) == []
        # The verdict is only reachable once the wait window has elapsed.
        assert elapsed >= _SLOT_WAIT_S - 0.1

    assert anonymous[0].status_code == 401
    assert anonymous[0].json()["error"]["code"] == "UNAUTHORIZED"
    assert forbidden[0].status_code == 403
    assert forbidden[0].json()["error"]["code"] == "FORBIDDEN"
    # Neither waited on the pool: the queue window is what they would have spent.
    assert anonymous[1] < _SLOT_WAIT_S / 2
    assert forbidden[1] < _SLOT_WAIT_S / 2

    assert [response.status_code for response in releases] == [201] * 4


@pytest.mark.asyncio
async def test_slots_are_released_after_success_and_business_failure(api) -> None:
    """Every completed ending releases the slot: the next attempt is admitted."""
    async with owned_connections(api) as world:
        world.bind(api.app)
        tenant, (member,) = await world.seed_tenant(api)
        headers = api.auth(member, tenant)
        gate = UploadGate()
        gate.install(api.console)

        # Success: after one peak of two, another peak of two is admitted.
        gate.hold()
        first = [
            asyncio.create_task(post_attachment(api.client, headers)),
            asyncio.create_task(post_attachment(api.client, headers)),
        ]
        await until(lambda: gate.entries >= 2, what="two uploads in flight")
        gate.release()
        assert [response.status_code for response in await _upload(first)] == [201, 201]

        # Business failure: the upstream rejects, so the handler answers 502.
        gate.fail = True
        failed = [
            asyncio.create_task(post_attachment(api.client, headers)),
            asyncio.create_task(post_attachment(api.client, headers)),
        ]
        failures = await _upload(failed)
        assert [response.status_code for response in failures] == [502, 502]
        assert {response.json()["error"]["code"] for response in failures} == {"DIFY_UPLOAD_FAILED"}

        # Retry after the failures: admitted at once instead of queueing behind
        # slots the failed requests would have leaked.
        gate.fail = False
        retried = [
            asyncio.create_task(post_attachment(api.client, headers)),
            asyncio.create_task(post_attachment(api.client, headers)),
        ]
        assert [response.status_code for response in await _upload(retried, timeout=5.0)] == [
            201,
            201,
        ]

    assert gate.peak == 2


@pytest.mark.asyncio
async def test_a_client_disconnect_releases_both_slots_at_once(api) -> None:
    """A lost connection frees the slots while the upstream call still runs.

    The pool is wrapped in ``PoolProbe``: its ``hold`` hands the handler the
    wrapped pool's lease, so a stand-in that yielded anything else would make
    the release fail inside the disconnect watcher and the retries would stay
    queued until the wait window ran out.
    """
    async with owned_connections(api) as world:
        world.bind(api.app)
        tenant, (member,) = await world.seed_tenant(api)
        headers = api.auth(member, tenant)
        gate = UploadGate()
        gate.install(api.console)
        gate.hold()
        probe = PoolProbe(api.app.state.upload_slots)
        api.app.state.upload_slots = probe

        dropped = [raw_upload(api.app, headers), raw_upload(api.app, headers)]
        retried: list = []
        try:
            for request in dropped:
                request.start()
            await until(lambda: gate.entries >= 2, what="both slots are held at the upstream gate")

            # The connection goes away while both uploads sit at the upstream;
            # the app is not cancelled, so only a released slot lets the retry in.
            for request in dropped:
                request.disconnect()

            retried = [raw_upload(api.app, headers), raw_upload(api.app, headers)]
            for request in retried:
                request.start()
            await until(
                lambda: gate.entries >= 4,
                what="the retries entered the handler while the disconnected uploads wait upstream",
                timeout=5.0,
            )
            gate.release()
            responses = [await request.wait() for request in retried]
            await asyncio.gather(*(request.wait() for request in dropped))
        finally:
            gate.release()
            for request in [*dropped, *retried]:
                request.abort()
                with suppress(asyncio.CancelledError):
                    await request.wait(timeout=5.0)

    assert [response.status for response in responses] == [201, 201]
    assert gate.peak == 4
    assert probe.admitted == 0, "a slot stayed counted as held after every exchange ended"
