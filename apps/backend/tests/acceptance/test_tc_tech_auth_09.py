"""TC-tech-auth-09 — an enterprise first login meets a local account's address.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-auth-09 → AC-T15) over
``docs/02-contracts.md`` C-23 / C-24 / C-114, carrying F7 (no self-registration
and no source switch). Every step goes through the fake identity provider and
the real callback over the ASGI stack and PostgreSQL, so the redirect and the
rows the callback leaves behind are judged as the browser would see them.
"""

from __future__ import annotations

import asyncio

import pytest
from harness import (
    CALLBACK_PATH,
    FRONTEND_HOME,
    FRONTEND_LOGIN,
    REFRESH_PATH,
    FakeIdp,
    auth,
    begin_login,
    count_rows,
    refresh_cookie,
    seed_tenant,
    seed_user,
    start,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken
from app.models.user import ACCOUNT_SOURCE_LOCAL, ACCOUNT_SOURCE_SSO, User

TC_ID = "TC-tech-auth-09"

_PASSWORD = "handover-passphrase-42"
_ME_PATH = "/api/me"
# Holds both callbacks at the write boundary until each has read the roster: the
# window an unserialised pair would slip through the address pre-check.
_RACE_WINDOW_SECONDS = 0.2


@pytest.mark.asyncio
async def test_a_local_account_blocks_the_enterprise_first_login(session_factory) -> None:  # type: ignore[no-untyped-def]
    """An occupied address is refused with ACCOUNT_EXISTS and nothing is written (C-114)."""
    tenant = await seed_tenant(session_factory)
    held = await seed_user(
        session_factory,
        tenant,
        email="alice@acme.com",
        password=_PASSWORD,
        must_change_password=False,
    )
    before = (
        held.id,
        held.account_source,
        held.status,
        held.password_hash,
        held.must_change_password,
    )
    assert before[1] == ACCOUNT_SOURCE_LOCAL

    idp = FakeIdp(sub="user-2", email="alice@acme.com")
    harness = await start(session_factory, idp=idp)
    try:
        state = await begin_login(harness.http, idp)
        response = await harness.http.get(
            CALLBACK_PATH,
            params={"code": "c1", "state": state},
            follow_redirects=False,
        )
        assert response.status_code == 302, response.text
        assert response.headers["location"] == f"{FRONTEND_LOGIN}?error=ACCOUNT_EXISTS"
        assert "set-cookie" not in response.headers
        assert response.content == b""

        async with session_factory() as session:
            rows = (
                await session.scalars(
                    select(User).where(
                        User.tenant_id == tenant.id,
                        func.lower(User.email) == "alice@acme.com",
                    )
                )
            ).all()
        assert len(rows) == 1
        after = rows[0]
        assert (
            after.id,
            after.account_source,
            after.status,
            after.password_hash,
            after.must_change_password,
        ) == before

        assert (
            await count_rows(
                session_factory,
                select(func.count())
                .select_from(RefreshToken)
                .where(RefreshToken.user_id == held.id),
            )
            == 0
        )
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_an_unoccupied_address_becomes_an_enterprise_account(session_factory) -> None:  # type: ignore[no-untyped-def]
    """302 to the workspace with a session, and the new row is an SSO employee (C-23)."""
    tenant = await seed_tenant(session_factory)
    idp = FakeIdp(sub="user-3", email="bob@acme.com")
    harness = await start(session_factory, idp=idp)
    try:
        state = await begin_login(harness.http, idp)
        response = await harness.http.get(
            CALLBACK_PATH,
            params={"code": "c1", "state": state},
            follow_redirects=False,
        )
        assert response.status_code == 302, response.text
        assert response.headers["location"] == FRONTEND_HOME
        cookie = refresh_cookie(response)

        refreshed = await harness.http.post(REFRESH_PATH, cookies={"refresh_token": cookie})
        assert refreshed.status_code == 200, refreshed.text
        me = await harness.http.get(_ME_PATH, headers=auth(refreshed.json()["data"]["accessToken"]))
        assert me.status_code == 200, me.text
        assert me.json()["data"]["email"] == "bob@acme.com"

        async with session_factory() as session:
            created = await session.scalar(
                select(User).where(
                    User.tenant_id == tenant.id,
                    func.lower(User.email) == "bob@acme.com",
                )
            )
        assert created is not None
        assert created.sso_sub == "user-3"
        assert created.role == "employee"
        assert created.status == "active"
        assert created.account_source == ACCOUNT_SOURCE_SSO
        assert created.password_hash is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_a_repeat_login_reuses_the_same_account(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The find-or-create step stays idempotent for a returning identity (C-114)."""
    tenant = await seed_tenant(session_factory)
    idp = FakeIdp(sub="user-4", email="carol@acme.com")
    harness = await start(session_factory, idp=idp)
    try:
        for _ in range(2):
            state = await begin_login(harness.http, idp)
            response = await harness.http.get(
                CALLBACK_PATH,
                params={"code": "c1", "state": state},
                follow_redirects=False,
            )
            assert response.status_code == 302, response.text
            assert response.headers["location"] == FRONTEND_HOME

        async with session_factory() as session:
            rows = (
                await session.scalars(
                    select(User).where(
                        User.tenant_id == tenant.id,
                        func.lower(User.email) == "carol@acme.com",
                    )
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].account_source == ACCOUNT_SOURCE_SSO
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_a_subject_in_the_local_namespace_addresses_no_account(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A provider subject under the local prefix is refused, not matched (C-114).

    Local accounts keep their subject in that namespace, so an identity provider
    asserting one must not be resolved to the account holding it — and must not
    be created as an enterprise account either. The callback refuses the claim
    and leaves both the account and the roster untouched.
    """
    tenant = await seed_tenant(session_factory)
    local = await seed_user(session_factory, tenant, email="carol@acme.com", password=_PASSWORD)
    idp = FakeIdp(sub=local.sso_sub, email="carol@acme.com")
    harness = await start(session_factory, idp=idp)
    try:
        state = await begin_login(harness.http, idp)
        response = await harness.http.get(
            CALLBACK_PATH,
            params={"code": "c1", "state": state},
            follow_redirects=False,
        )
        assert response.status_code == 302, response.text
        assert response.headers["location"] == f"{FRONTEND_LOGIN}?error=INVALID_ID_TOKEN"
        assert "set-cookie" not in response.headers
        assert response.content == b""

        async with session_factory() as session:
            stored = await session.scalar(select(User).where(User.id == local.id))
        assert stored is not None
        assert stored.account_source == ACCOUNT_SOURCE_LOCAL
        assert stored.status == "active"
        assert stored.must_change_password is False

        total = await count_rows(
            session_factory,
            select(func.count()).select_from(User).where(User.tenant_id == tenant.id),
        )
        assert total == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_two_callbacks_racing_for_one_address_land_one_account(  # type: ignore[no-untyped-def]
    session_factory, monkeypatch
) -> None:
    """Callbacks that race past the address check answer the conflict, not a 500 (C-23).

    Two identities claim the same address in different case, and each holds its
    write until both have read the roster — so the lookup cannot separate them
    and the uniqueness rule in the database decides instead. The callback that
    loses must present that verdict the way the sequential path does: a redirect
    to the login page carrying ACCOUNT_EXISTS and no session (C-24), with the
    address left as a single row.
    """
    tenant = await seed_tenant(session_factory)
    lower = FakeIdp(sub="user-race-lower", email="dana@acme.com")
    upper = FakeIdp(sub="user-race-upper", email="Dana@acme.com")
    flush = AsyncSession.flush

    async def held_flush(session, *args, **kwargs):  # type: ignore[no-untyped-def]
        await asyncio.sleep(_RACE_WINDOW_SECONDS)
        return await flush(session, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", held_flush)
    first = await start(session_factory, idp=lower)
    second = await start(session_factory, idp=upper)
    try:
        states = [
            await begin_login(first.http, lower),
            await begin_login(second.http, upper),
        ]
        responses = await asyncio.gather(
            *(
                harness.http.get(
                    CALLBACK_PATH,
                    params={"code": "c1", "state": state},
                    follow_redirects=False,
                )
                for harness, state in zip((first, second), states, strict=True)
            )
        )
        assert [response.status_code for response in responses] == [302, 302], [
            response.text for response in responses
        ]
        assert sorted(response.headers["location"] for response in responses) == sorted(
            [FRONTEND_HOME, f"{FRONTEND_LOGIN}?error=ACCOUNT_EXISTS"]
        )
        accepted = next(
            response for response in responses if response.headers["location"] == FRONTEND_HOME
        )
        refused = next(
            response for response in responses if response.headers["location"] != FRONTEND_HOME
        )
        assert refresh_cookie(accepted)
        assert "set-cookie" not in refused.headers
        assert refused.content == b""

        async with session_factory() as session:
            rows = (
                await session.scalars(
                    select(User).where(
                        User.tenant_id == tenant.id,
                        func.lower(User.email) == "dana@acme.com",
                    )
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].account_source == ACCOUNT_SOURCE_SSO
        assert rows[0].sso_sub in {lower.sub, upper.sub}
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_a_provider_email_change_onto_another_accounts_address_is_refused(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """A returning identity cannot take an address another account holds (C-103 / C-24).

    The identity provider moves a known subject onto an address a second account
    of the tenant already holds — the case-folded match the uniqueness rule
    covers. The account keeps the address it was known by, the second account is
    untouched, and the callback presents the conflict as it does on the creation
    path rather than as an error body.
    """
    tenant = await seed_tenant(session_factory)
    returning = await seed_user(
        session_factory, tenant, email="erin@acme.com", sso_sub="user-returning"
    )
    holder = await seed_user(session_factory, tenant, email="frank@acme.com", sso_sub="user-holder")
    idp = FakeIdp(sub="user-returning", email="Frank@acme.com")
    harness = await start(session_factory, idp=idp)
    try:
        state = await begin_login(harness.http, idp)
        response = await harness.http.get(
            CALLBACK_PATH,
            params={"code": "c1", "state": state},
            follow_redirects=False,
        )
        assert response.status_code == 302, response.text
        assert response.headers["location"] == f"{FRONTEND_LOGIN}?error=ACCOUNT_EXISTS"
        assert "set-cookie" not in response.headers
        assert response.content == b""

        async with session_factory() as session:
            stored = (
                await session.scalars(select(User).where(User.id.in_([returning.id, holder.id])))
            ).all()
        assert {user.sso_sub: user.email for user in stored} == {
            "user-returning": "erin@acme.com",
            "user-holder": "frank@acme.com",
        }
        assert (
            await count_rows(
                session_factory,
                select(func.count()).select_from(User).where(User.tenant_id == tenant.id),
            )
            == 2
        )
    finally:
        await harness.close()
