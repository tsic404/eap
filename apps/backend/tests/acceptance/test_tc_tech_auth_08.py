"""TC-tech-auth-08 — disabling an account is refused on both sides (F10 negative).

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-auth-08 → AC-T15) over
``docs/02-contracts.md`` C-116 / C-29, reusing C-28 / C-111 / C-3. Every step is
driven over HTTP against the real ASGI stack and PostgreSQL: a disabled member
is refused at the login credential step and at identity resolution, while the
tokens already handed out stay intact until each request is judged.
"""

from __future__ import annotations

import asyncio

import pytest
from harness import (
    LOGIN_PATH,
    MEMBERS_PATH,
    REFRESH_PATH,
    access_token,
    auth,
    error_code,
    refresh_cookie,
    seed_admin,
    seed_tenant,
    seed_user,
    start,
)
from sqlalchemy import func, select

from app.models.user import User
from app.services.member_service import MemberService

TC_ID = "TC-tech-auth-08"

_PASSWORD = "handover-passphrase-42"
_ME_PATH = "/api/me"
# Holds two switches of one tenant in the window between the administrator count
# and the commit, where an unserialised pair would both count and both commit.
_RACE_WINDOW_SECONDS = 0.2


@pytest.mark.asyncio
async def test_disabling_a_member_switches_only_that_member(session_factory) -> None:  # type: ignore[no-untyped-def]
    """200 carries the new status and the roster keeps every other row (C-28)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    member = await seed_user(session_factory, tenant, email="member@acme.com", password=_PASSWORD)
    await seed_user(session_factory, tenant, email="other@acme.com", password=_PASSWORD)
    harness = await start(session_factory)
    try:
        headers = auth(access_token(admin, tenant))
        response = await harness.http.patch(
            f"{MEMBERS_PATH}/{member.id}", json={"status": "disabled"}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "disabled"
        assert response.json()["data"]["id"] == str(member.id)

        page = await harness.http.get(MEMBERS_PATH, headers=headers)
        assert page.status_code == 200, page.text
        by_email = {item["email"]: item for item in page.json()["data"]["items"]}
        assert by_email["member@acme.com"]["status"] == "disabled"
        assert by_email["other@acme.com"]["status"] == "active"
        assert by_email["admin@acme.com"]["status"] == "active"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_disabled_account_signing_in_is_indistinguishable_from_a_wrong_password(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """The correct password is refused exactly like a wrong one (C-116 / C-111 ⑥)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    member = await seed_user(session_factory, tenant, email="member@acme.com", password=_PASSWORD)
    harness = await start(session_factory)
    try:
        headers = auth(access_token(admin, tenant))
        disabled = await harness.http.patch(
            f"{MEMBERS_PATH}/{member.id}", json={"status": "disabled"}, headers=headers
        )
        assert disabled.status_code == 200, disabled.text

        refused = await harness.http.post(
            LOGIN_PATH, json={"email": "member@acme.com", "password": _PASSWORD}
        )
        wrong = await harness.http.post(
            LOGIN_PATH, json={"email": "member@acme.com", "password": "not-the-password"}
        )
        for response in (refused, wrong):
            assert response.status_code == 401, response.text
            assert error_code(response) == "INVALID_CREDENTIALS"
            assert "set-cookie" not in response.headers
        assert refused.json() == wrong.json()
        assert refused.content == wrong.content
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_access_token_issued_before_the_disable_is_refused(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Both identity and a member surface read the pre-disable token as absent (C-116)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    member = await seed_user(session_factory, tenant, email="member@acme.com", password=_PASSWORD)
    token = access_token(member, tenant)
    harness = await start(session_factory)
    try:
        headers = auth(access_token(admin, tenant))
        disabled = await harness.http.patch(
            f"{MEMBERS_PATH}/{member.id}", json={"status": "disabled"}, headers=headers
        )
        assert disabled.status_code == 200, disabled.text

        me = await harness.http.get(_ME_PATH, headers=auth(token))
        listed = await harness.http.get(MEMBERS_PATH, headers=auth(token))
        for response in (me, listed):
            assert response.status_code == 401, response.text
            assert error_code(response) == "UNAUTHORIZED"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_refresh_after_the_disable_still_rotates_but_the_token_is_refused(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """The refresh channel survives the disable; the token it mints does not (C-29)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    member = await seed_user(session_factory, tenant, email="member@acme.com", password=_PASSWORD)
    harness = await start(session_factory)
    try:
        login = await harness.http.post(
            LOGIN_PATH, json={"email": "member@acme.com", "password": _PASSWORD}
        )
        assert login.status_code == 200, login.text
        cookie = refresh_cookie(login)

        headers = auth(access_token(admin, tenant))
        disabled = await harness.http.patch(
            f"{MEMBERS_PATH}/{member.id}", json={"status": "disabled"}, headers=headers
        )
        assert disabled.status_code == 200, disabled.text

        refreshed = await harness.http.post(REFRESH_PATH, cookies={"refresh_token": cookie})
        assert refreshed.status_code == 200, refreshed.text
        assert refresh_cookie(refreshed)

        me = await harness.http.get(_ME_PATH, headers=auth(refreshed.json()["data"]["accessToken"]))
        assert me.status_code == 401, me.text
        assert error_code(me) == "UNAUTHORIZED"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_another_member_keeps_working(session_factory) -> None:  # type: ignore[no-untyped-def]
    """One member's disable leaves a colleague's session untouched (C-29)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    member = await seed_user(session_factory, tenant, email="member@acme.com", password=_PASSWORD)
    colleague = await seed_user(session_factory, tenant, email="colleague@acme.com")
    harness = await start(session_factory)
    try:
        headers = auth(access_token(admin, tenant))
        disabled = await harness.http.patch(
            f"{MEMBERS_PATH}/{member.id}", json={"status": "disabled"}, headers=headers
        )
        assert disabled.status_code == 200, disabled.text

        me = await harness.http.get(_ME_PATH, headers=auth(access_token(colleague, tenant)))
        assert me.status_code == 200, me.text
        assert me.json()["data"]["email"] == "colleague@acme.com"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_the_disable_invariants(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Last-admin, self-disable, tenancy, role and body rules all hold (C-29)."""
    tenant = await seed_tenant(session_factory)
    other = await seed_tenant(session_factory, domain="other.com", name="Other")
    admin = await seed_admin(session_factory, tenant)
    second = await seed_admin(session_factory, tenant, email="second@acme.com")
    third = await seed_admin(session_factory, tenant, email="third@acme.com")
    employee = await seed_user(session_factory, tenant, email="employee@acme.com")
    foreign = await seed_user(session_factory, other, email="foreign@other.com")
    harness = await start(session_factory)
    try:
        admin_headers = auth(access_token(admin, tenant))

        # A non-administrator never reaches the status switch.
        forbidden = await harness.http.patch(
            f"{MEMBERS_PATH}/{employee.id}",
            json={"status": "disabled"},
            headers=auth(access_token(employee, tenant)),
        )
        assert forbidden.status_code == 403, forbidden.text
        assert error_code(forbidden) == "FORBIDDEN"

        # Another tenant's member identifier simply does not resolve.
        absent = await harness.http.patch(
            f"{MEMBERS_PATH}/{foreign.id}", json={"status": "disabled"}, headers=admin_headers
        )
        assert absent.status_code == 404, absent.text
        assert error_code(absent) == "NOT_FOUND"

        # Only the two status values are accepted, and no other field is.
        paused = await harness.http.patch(
            f"{MEMBERS_PATH}/{employee.id}", json={"status": "paused"}, headers=admin_headers
        )
        role = await harness.http.patch(
            f"{MEMBERS_PATH}/{employee.id}", json={"role": "employee"}, headers=admin_headers
        )
        for response in (paused, role):
            assert response.status_code == 422, response.text
            assert error_code(response) == "VALIDATION_ERROR"

        # The last-platform-administrator rule outranks self-disable: with two
        # administrators still active, disabling oneself is a self-disable, and
        # once one is left the same request answers LAST_ADMIN (C-29).
        removed = await harness.http.patch(
            f"{MEMBERS_PATH}/{third.id}", json={"status": "disabled"}, headers=admin_headers
        )
        assert removed.status_code == 200, removed.text

        self_disable = await harness.http.patch(
            f"{MEMBERS_PATH}/{second.id}",
            json={"status": "disabled"},
            headers=auth(access_token(second, tenant)),
        )
        assert self_disable.status_code == 409, self_disable.text
        assert error_code(self_disable) == "SELF_DISABLE"

        remaining = await harness.http.patch(
            f"{MEMBERS_PATH}/{second.id}", json={"status": "disabled"}, headers=admin_headers
        )
        assert remaining.status_code == 200, remaining.text

        last_admin = await harness.http.patch(
            f"{MEMBERS_PATH}/{admin.id}", json={"status": "disabled"}, headers=admin_headers
        )
        assert last_admin.status_code == 409, last_admin.text
        assert error_code(last_admin) == "LAST_ADMIN"

        async with session_factory() as session:
            still_active = await session.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.tenant_id == tenant.id,
                    User.role == "platform_admin",
                    User.status == "active",
                )
            )
        assert still_active == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_two_administrators_cannot_disable_each_other_at_once(  # type: ignore[no-untyped-def]
    session_factory, monkeypatch
) -> None:
    """Switches that run at once still leave an administrator standing (C-29).

    Each switch counts the tenant's administrators before it writes a status, so
    the count is delayed until both requests have reached it: that is the window
    in which both would count the other as active and both commit, leaving the
    tenant without an administrator. A switch that goes first must therefore
    hold the other one off until it committed, and the refused one reports the
    same code a sequential switch does.
    """
    tenant = await seed_tenant(session_factory)
    first = await seed_admin(session_factory, tenant)
    second = await seed_admin(session_factory, tenant, email="second@acme.com")
    count_admins = MemberService._admins_left

    async def delayed_count(session, tenant):  # type: ignore[no-untyped-def]
        counted = await count_admins(session, tenant)
        await asyncio.sleep(_RACE_WINDOW_SECONDS)
        return counted

    monkeypatch.setattr(MemberService, "_admins_left", staticmethod(delayed_count))
    harness = await start(session_factory)
    try:
        responses = await asyncio.gather(
            *(
                harness.http.patch(
                    f"{MEMBERS_PATH}/{target.id}",
                    json={"status": "disabled"},
                    headers=auth(access_token(actor, tenant)),
                )
                for target, actor in ((second, first), (first, second))
            )
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        refused = next(response for response in responses if response.status_code == 409)
        assert error_code(refused) == "LAST_ADMIN"

        async with session_factory() as session:
            still_active = await session.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.tenant_id == tenant.id,
                    User.role == "platform_admin",
                    User.status == "active",
                )
            )
        assert still_active == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_re_enabling_restores_the_login(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Switching back to active admits the same credential again (C-116)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    member = await seed_user(session_factory, tenant, email="member@acme.com", password=_PASSWORD)
    harness = await start(session_factory)
    try:
        headers = auth(access_token(admin, tenant))
        for status in ("disabled", "active"):
            switched = await harness.http.patch(
                f"{MEMBERS_PATH}/{member.id}", json={"status": status}, headers=headers
            )
            assert switched.status_code == 200, switched.text
            assert switched.json()["data"]["status"] == status

        login = await harness.http.post(
            LOGIN_PATH, json={"email": "member@acme.com", "password": _PASSWORD}
        )
        assert login.status_code == 200, login.text
        assert login.json() == {"data": {"mustChangePassword": False}}
    finally:
        await harness.close()
