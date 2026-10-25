"""TC-tech-auth-10 — the format rule on every password an administrator sets.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-auth-10 → AC-T15) over
``docs/02-contracts.md`` C-113 / C-114 / C-115 / C-117. Driven over HTTP against
the real ASGI stack and PostgreSQL, so the status, the field details, the stored
credential and the refresh families are judged on what the system actually did.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from harness import (
    LOGIN_PATH,
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
from sqlalchemy import func, select

from app.auth.password import verify_password
from app.bootstrap import seed_local_family
from app.config import Settings
from app.models.tenant import Tenant
from app.models.user import User

TC_ID = "TC-tech-auth-10"

_PASSWORD = "member-passphrase-10"
_SHORT = "seven77"  # 7 characters
_LONG = "long-" + "x" * 124  # 129 characters
_MIN = "eight888"  # 8 characters
_MAX = "m" * 128  # 128 characters
_EMAIL = "member@acme.com"


def _new_member(email: str, password: str) -> dict[str, str]:
    """A local-account creation body carrying ``password``."""
    return {"email": email, "name": "New Comer", "password": password}


def _cookies(value: str) -> dict[str, str]:
    """Send exactly one refresh cookie, so a client jar cannot substitute another."""
    return {"Cookie": f"refresh_token={value}"}


async def _refresh(http: httpx.AsyncClient, cookie: str) -> httpx.Response:
    """Present a refresh cookie to the rotation endpoint (C-25)."""
    return await http.post(REFRESH_PATH, headers=_cookies(cookie))


async def _sign_in(http: httpx.AsyncClient, *, email: str, password: str) -> httpx.Response:
    """POST the local login path (C-111)."""
    return await http.post(LOGIN_PATH, json={"email": email, "password": password})


async def _load_user(session_factory: Any, user_id: uuid.UUID) -> User:
    """Read a member row back, for its stored credential and forced-change flag."""
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.id == user_id))
    assert user is not None
    return user


async def _count_members(session_factory: Any, tenant: Tenant) -> int:
    """How many accounts the tenant holds, so a refused create proves no write."""
    async with session_factory() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(User).where(User.tenant_id == tenant.id)
            )
            or 0
        )


async def _reset(
    http: httpx.AsyncClient, token: str, member_id: uuid.UUID, *, password: str
) -> httpx.Response:
    """Ask an administrator to set ``password`` on a member (C-115)."""
    return await http.post(
        f"{MEMBERS_PATH}/{member_id}/password",
        json={"password": password},
        headers=auth(token),
    )


def _assert_password_field_error(response: httpx.Response, *, value: str) -> None:
    """422 with one field error pointing at ``password`` and no echoed value."""
    assert response.status_code == 422, response.text
    assert error_code(response) == "VALIDATION_ERROR"
    assert [detail["loc"][-1] for detail in error_details(response)] == ["password"]
    assert value not in response.text


@pytest.mark.asyncio
async def test_creation_refuses_out_of_range_initial_passwords(session_factory) -> None:  # type: ignore[no-untyped-def]
    """7 and 129 characters answer 422 for the password field and create nothing (C-114)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        token = access_token(admin, tenant)
        before = await _count_members(session_factory, tenant)

        for password in (_SHORT, _LONG):
            response = await harness.http.post(
                MEMBERS_PATH,
                json=_new_member(f"newcomer-{len(password)}@acme.com", password),
                headers=auth(token),
            )
            _assert_password_field_error(response, value=password)

        assert await _count_members(session_factory, tenant) == before
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_creation_answers_the_address_rules_before_the_password_rule(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """A combined hit returns the domain / uniqueness answer, not a password error (C-114)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    await seed_user(session_factory, tenant, email="taken@acme.com", password=_PASSWORD)
    harness = await start(session_factory)
    try:
        token = access_token(admin, tenant)

        outside = await harness.http.post(
            MEMBERS_PATH,
            json=_new_member("outsider@other.com", _SHORT),
            headers=auth(token),
        )
        assert outside.status_code == 422, outside.text
        assert error_code(outside) == "VALIDATION_ERROR"
        assert [detail["loc"][-1] for detail in error_details(outside)] == ["email"]
        assert _SHORT not in outside.text

        taken = await harness.http.post(
            MEMBERS_PATH,
            json=_new_member("taken@acme.com", _SHORT),
            headers=auth(token),
        )
        assert taken.status_code == 409, taken.text
        assert error_code(taken) == "ACCOUNT_EXISTS"
        assert error_details(taken) == []
        assert _SHORT not in taken.text
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_reset_refusals_leave_the_account_and_its_session_untouched(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """7, 129 and the current password answer 422 with nothing written (C-115)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    member = await seed_user(session_factory, tenant, email=_EMAIL, password=_PASSWORD)
    harness = await start(session_factory)
    try:
        token = access_token(admin, tenant)
        before = await _load_user(session_factory, member.id)
        assert before.password_hash is not None

        # A live session established before the refusals: its rotated cookie is
        # the credential that must still work afterwards.
        login = await _sign_in(harness.http, email=_EMAIL, password=_PASSWORD)
        assert login.status_code == 200, login.text
        rotated = await _refresh(harness.http, refresh_cookie(login))
        assert rotated.status_code == 200, rotated.text
        live_cookie = refresh_cookie(rotated)

        for password in (_SHORT, _LONG, _PASSWORD):
            response = await _reset(harness.http, token, member.id, password=password)
            _assert_password_field_error(response, value=password)

        after = await _load_user(session_factory, member.id)
        assert after.password_hash == before.password_hash
        assert after.must_change_password == before.must_change_password
        assert verify_password(_PASSWORD, after.password_hash)

        still_live = await _refresh(harness.http, live_cookie)
        assert still_live.status_code == 200, still_live.text
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_the_boundaries_are_accepted_on_both_paths(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Exactly 8 and 128 characters are usable for a create and for a reset (C-113)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    member = await seed_user(session_factory, tenant, email=_EMAIL, password=_PASSWORD)
    harness = await start(session_factory)
    try:
        token = access_token(admin, tenant)

        for index, password in enumerate((_MIN, _MAX)):
            created = await harness.http.post(
                MEMBERS_PATH,
                json=_new_member(f"boundary-{index}@acme.com", password),
                headers=auth(token),
            )
            assert created.status_code == 201, created.text
            made = await _load_user(session_factory, uuid.UUID(created.json()["data"]["id"]))
            assert made.password_hash is not None
            assert verify_password(password, made.password_hash)

        # The first reset makes _MIN the current password; the second one must
        # therefore use a different boundary value and still be accepted.
        for password in (_MIN, _MAX):
            reset = await _reset(harness.http, token, member.id, password=password)
            assert reset.status_code == 200, reset.text

        stored = await _load_user(session_factory, member.id)
        assert stored.password_hash is not None
        assert verify_password(_MAX, stored.password_hash)
        assert not verify_password(_PASSWORD, stored.password_hash)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_missing_targets_and_a_closed_capability_answer_404(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The 404s precede the password rule, so a bad password cannot surface as 422."""
    tenant = await seed_tenant(session_factory)
    other = await seed_tenant(session_factory, domain="other.com", name="Other")
    admin = await seed_admin(session_factory, tenant)
    foreign = await seed_user(session_factory, other, email="foreign@other.com", password=_PASSWORD)
    member = await seed_user(session_factory, tenant, email=_EMAIL, password=_PASSWORD)

    harness = await start(session_factory)
    try:
        token = access_token(admin, tenant)
        for member_id in (uuid.uuid4(), foreign.id):
            response = await _reset(harness.http, token, member_id, password=_SHORT)
            assert response.status_code == 404, response.text
            assert error_code(response) == "NOT_FOUND"
    finally:
        await harness.close()

    closed = await start(session_factory, local_login_enabled=False)
    try:
        token = access_token(admin, tenant)
        created = await closed.http.post(
            MEMBERS_PATH, json=_new_member("closed@acme.com", _SHORT), headers=auth(token)
        )
        reset = await _reset(closed.http, token, member.id, password=_SHORT)
        for response in (created, reset):
            assert response.status_code == 404, response.text
            assert error_code(response) == "NOT_FOUND"
    finally:
        await closed.close()


@pytest.mark.asyncio
async def test_the_seeding_path_refuses_an_out_of_range_admin_password(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A weak seeded credential fails the one-shot bootstrap instead of landing (C-117)."""
    settings = Settings(
        _env_file=None,
        local_login_enabled=True,
        local_admin_email="admin@acme.com",
        local_admin_password=_SHORT,
    )

    with pytest.raises(RuntimeError, match="LOCAL_ADMIN_PASSWORD"):
        async with session_factory() as session:
            await seed_local_family(session, settings)

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 0
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 0
