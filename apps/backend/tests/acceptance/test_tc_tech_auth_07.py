"""TC-tech-auth-07 — the forced change and the session end a password write causes.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-auth-07 → AC-T15) over
``docs/02-contracts.md`` C-113 / C-115, reusing C-3 / C-25 / C-29. Every case
drives the real ASGI stack against PostgreSQL, so the gate, the refresh families
and the stored credential are judged on what the system actually did.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from harness import (
    CHANGE_PASSWORD_PATH,
    LOGIN_PATH,
    LOGOUT_PATH,
    MEMBERS_PATH,
    REFRESH_PATH,
    access_token,
    auth,
    error_code,
    error_details,
    refresh_cookie,
    seed_admin,
    seed_tenant,
    seed_user,
    start,
)
from sqlalchemy import select

from app.auth.dependencies import PASSWORD_CHANGE_REQUIRED
from app.auth.password import verify_password
from app.models.user import User

TC_ID = "TC-tech-auth-07"

_PASSWORD = "forced-change-passphrase-30"
_NEW_PASSWORD = "chosen-by-the-member-31"
_RESET_PASSWORD = "reset-by-the-admin-32"
_WRONG_PASSWORD = "not-the-current-one-33"
_SPARE_PASSWORD = "another-passphrase-34"
_SHORT_PASSWORD = "seven77"
_TOO_LONG_PASSWORD = "long-" + "x" * 124
_EMAIL = "forced@acme.com"


def _cookies(value: str) -> dict[str, str]:
    """Send exactly one refresh cookie, so a client jar cannot substitute another."""
    return {"Cookie": f"refresh_token={value}"}


async def _sign_in(http: httpx.AsyncClient, *, email: str, password: str) -> httpx.Response:
    """POST the local login path (C-111)."""
    return await http.post(LOGIN_PATH, json={"email": email, "password": password})


async def _refresh(http: httpx.AsyncClient, cookie: str) -> httpx.Response:
    """Present a refresh cookie to the rotation endpoint (C-25)."""
    return await http.post(REFRESH_PATH, headers=_cookies(cookie))


async def _change_password(
    http: httpx.AsyncClient, token: str, *, current: str, new: str
) -> httpx.Response:
    """Change the caller's own password with a bearer token (C-113)."""
    return await http.post(
        CHANGE_PASSWORD_PATH,
        json={"currentPassword": current, "newPassword": new},
        headers=auth(token),
    )


async def _load_user(session_factory: Any, user_id: uuid.UUID) -> User:
    """Read a member row back, for its stored credential and forced-change flag."""
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
    assert user is not None
    return user


@pytest.mark.asyncio
async def test_a_forced_change_blocks_business_requests_but_not_the_exempt_paths(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """403 everywhere except change, refresh and logout, whatever token is held (C-113)."""
    tenant = await seed_tenant(session_factory)
    await seed_user(
        session_factory, tenant, email=_EMAIL, password=_PASSWORD, must_change_password=True
    )
    harness = await start(session_factory)
    try:
        login = await _sign_in(harness.http, email=_EMAIL, password=_PASSWORD)
        assert login.status_code == 200, login.text
        assert login.json() == {"data": {"mustChangePassword": True}}

        # The forced-change account still obtains an access token — the refresh
        # path is exempt — and that token is under the same gate.
        refreshed = await _refresh(harness.http, refresh_cookie(login))
        assert refreshed.status_code == 200, refreshed.text
        token = refreshed.json()["data"]["accessToken"]

        for path in ("/api/me", MEMBERS_PATH):
            blocked = await harness.http.get(path, headers=auth(token))
            assert blocked.status_code == 403, f"{path}: {blocked.text}"
            assert error_code(blocked) == PASSWORD_CHANGE_REQUIRED

        rotated = await _refresh(harness.http, refresh_cookie(refreshed))
        assert rotated.status_code == 200, rotated.text
        token_after_rotation = rotated.json()["data"]["accessToken"]
        still_blocked = await harness.http.get("/api/me", headers=auth(token_after_rotation))
        assert still_blocked.status_code == 403, still_blocked.text
        assert error_code(still_blocked) == PASSWORD_CHANGE_REQUIRED

        logout = await harness.http.post(
            LOGOUT_PATH,
            headers={**auth(token_after_rotation), **_cookies(refresh_cookie(rotated))},
        )
        assert logout.status_code == 200, logout.text
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_a_successful_change_clears_the_flag(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The change stores the new password, clears the marker and re-admits the account."""
    tenant = await seed_tenant(session_factory)
    member = await seed_user(
        session_factory, tenant, email=_EMAIL, password=_PASSWORD, must_change_password=True
    )
    harness = await start(session_factory)
    try:
        login = await _sign_in(harness.http, email=_EMAIL, password=_PASSWORD)
        assert login.status_code == 200, login.text
        refreshed = await _refresh(harness.http, refresh_cookie(login))
        assert refreshed.status_code == 200, refreshed.text

        changed = await _change_password(
            harness.http,
            refreshed.json()["data"]["accessToken"],
            current=_PASSWORD,
            new=_NEW_PASSWORD,
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["data"]["message"]

        stored = await _load_user(session_factory, member.id)
        assert stored.must_change_password is False
        assert stored.password_hash is not None
        assert verify_password(_NEW_PASSWORD, stored.password_hash)
        assert not verify_password(_PASSWORD, stored.password_hash)

        # A token minted after the change reaches the business surface again.
        again = await _sign_in(harness.http, email=_EMAIL, password=_NEW_PASSWORD)
        assert again.status_code == 200, again.text
        assert again.json() == {"data": {"mustChangePassword": False}}
        second = await _refresh(harness.http, refresh_cookie(again))
        assert second.status_code == 200, second.text

        me = await harness.http.get("/api/me", headers=auth(second.json()["data"]["accessToken"]))
        assert me.status_code == 200, me.text
        assert me.json()["data"]["email"] == _EMAIL
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_the_pre_change_refresh_cookie_no_longer_refreshes(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The change ends every refresh family of the account, so the old cookie is dead."""
    tenant = await seed_tenant(session_factory)
    await seed_user(
        session_factory, tenant, email=_EMAIL, password=_PASSWORD, must_change_password=True
    )
    harness = await start(session_factory)
    try:
        login = await _sign_in(harness.http, email=_EMAIL, password=_PASSWORD)
        assert login.status_code == 200, login.text
        refreshed = await _refresh(harness.http, refresh_cookie(login))
        assert refreshed.status_code == 200, refreshed.text
        # The rotated cookie is the credential the account held before the
        # change; the change must end the family it belongs to.
        pre_change = refresh_cookie(refreshed)

        changed = await _change_password(
            harness.http,
            refreshed.json()["data"]["accessToken"],
            current=_PASSWORD,
            new=_NEW_PASSWORD,
        )
        assert changed.status_code == 200, changed.text

        replay = await _refresh(harness.http, pre_change)
        assert replay.status_code == 401, replay.text
        assert "set-cookie" not in replay.headers
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_an_access_token_issued_before_the_change_keeps_working(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Existing access tokens are not revoked at once — only the refresh families (C-113)."""
    tenant = await seed_tenant(session_factory)
    await seed_user(
        session_factory, tenant, email=_EMAIL, password=_PASSWORD, must_change_password=True
    )
    harness = await start(session_factory)
    try:
        login = await _sign_in(harness.http, email=_EMAIL, password=_PASSWORD)
        assert login.status_code == 200, login.text
        refreshed = await _refresh(harness.http, refresh_cookie(login))
        assert refreshed.status_code == 200, refreshed.text
        before_change = refreshed.json()["data"]["accessToken"]

        changed = await _change_password(
            harness.http, before_change, current=_PASSWORD, new=_NEW_PASSWORD
        )
        assert changed.status_code == 200, changed.text

        # The same bearer token now passes the gate, because the marker is gone
        # and the token itself is still inside its own 900 s window.
        me = await harness.http.get("/api/me", headers=auth(before_change))
        assert me.status_code == 200, me.text
        assert me.json()["data"]["email"] == _EMAIL
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_login_after_the_change_accepts_only_the_new_password(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The replaced password stops authenticating; the new one lands changed (C-111)."""
    tenant = await seed_tenant(session_factory)
    await seed_user(
        session_factory, tenant, email=_EMAIL, password=_PASSWORD, must_change_password=True
    )
    harness = await start(session_factory)
    try:
        login = await _sign_in(harness.http, email=_EMAIL, password=_PASSWORD)
        assert login.status_code == 200, login.text
        refreshed = await _refresh(harness.http, refresh_cookie(login))
        assert refreshed.status_code == 200, refreshed.text
        changed = await _change_password(
            harness.http,
            refreshed.json()["data"]["accessToken"],
            current=_PASSWORD,
            new=_NEW_PASSWORD,
        )
        assert changed.status_code == 200, changed.text

        stale = await _sign_in(harness.http, email=_EMAIL, password=_PASSWORD)
        assert stale.status_code == 401, stale.text
        assert error_code(stale) == "INVALID_CREDENTIALS"

        fresh = await _sign_in(harness.http, email=_EMAIL, password=_NEW_PASSWORD)
        assert fresh.status_code == 200, fresh.text
        assert fresh.json() == {"data": {"mustChangePassword": False}}
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_the_new_password_rule_is_enforced(session_factory) -> None:  # type: ignore[no-untyped-def]
    """8–128 characters and different from the old one; a wrong proof stores nothing."""
    tenant = await seed_tenant(session_factory)
    member = await seed_user(session_factory, tenant, email=_EMAIL, password=_PASSWORD)
    harness = await start(session_factory)
    try:
        token = access_token(member, tenant)
        before = (await _load_user(session_factory, member.id)).password_hash
        assert before is not None

        for rejected in (_SHORT_PASSWORD, _TOO_LONG_PASSWORD):
            short_or_long = await _change_password(
                harness.http, token, current=_PASSWORD, new=rejected
            )
            assert short_or_long.status_code == 422, short_or_long.text
            assert error_code(short_or_long) == "VALIDATION_ERROR"
            assert [detail["loc"][-1] for detail in error_details(short_or_long)] == ["newPassword"]

        unchanged = await _change_password(harness.http, token, current=_PASSWORD, new=_PASSWORD)
        assert unchanged.status_code == 422, unchanged.text
        assert error_code(unchanged) == "VALIDATION_ERROR"
        assert [detail["loc"][-1] for detail in error_details(unchanged)] == ["newPassword"]

        unproven = await _change_password(
            harness.http, token, current=_WRONG_PASSWORD, new=_SPARE_PASSWORD
        )
        assert unproven.status_code == 401, unproven.text
        assert error_code(unproven) == "INVALID_CREDENTIALS"

        after = (await _load_user(session_factory, member.id)).password_hash
        assert after == before
        assert verify_password(_PASSWORD, after)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_an_admin_reset_ends_the_session_and_forces_a_change(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A reset sets the new credential, ends the member's families and re-arms the flag."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    member = await seed_user(session_factory, tenant, email=_EMAIL, password=_PASSWORD)
    harness = await start(session_factory)
    try:
        login = await _sign_in(harness.http, email=_EMAIL, password=_PASSWORD)
        assert login.status_code == 200, login.text
        pre_reset = refresh_cookie(login)

        reset = await harness.http.post(
            f"{MEMBERS_PATH}/{member.id}/password",
            json={"password": _RESET_PASSWORD},
            headers=auth(access_token(admin, tenant)),
        )
        assert reset.status_code == 200, reset.text

        replay = await _refresh(harness.http, pre_reset)
        assert replay.status_code == 401, replay.text
        assert "set-cookie" not in replay.headers

        relogin = await _sign_in(harness.http, email=_EMAIL, password=_RESET_PASSWORD)
        assert relogin.status_code == 200, relogin.text
        assert relogin.json() == {"data": {"mustChangePassword": True}}

        stored = await _load_user(session_factory, member.id)
        assert stored.must_change_password is True
        assert stored.password_hash is not None
        assert verify_password(_RESET_PASSWORD, stored.password_hash)
        assert not verify_password(_PASSWORD, stored.password_hash)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_an_sso_member_has_no_password_to_reset(session_factory) -> None:  # type: ignore[no-untyped-def]
    """C-115 covers the local accounts only; an SSO member reads as absent."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    sso = await seed_user(session_factory, tenant, email="sso@acme.com")
    harness = await start(session_factory)
    try:
        reset = await harness.http.post(
            f"{MEMBERS_PATH}/{sso.id}/password",
            json={"password": _RESET_PASSWORD},
            headers=auth(access_token(admin, tenant)),
        )
        assert reset.status_code == 404, reset.text
        assert error_code(reset) == "NOT_FOUND"
    finally:
        await harness.close()
