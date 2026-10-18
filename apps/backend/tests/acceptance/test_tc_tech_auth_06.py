"""TC-tech-auth-06 — passwords never leave as plaintext or a reversible form.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-auth-06 → AC-T15) over
``docs/02-contracts.md`` C-115 / C-113 (the F8 negative). Every case drives the
real ASGI stack against PostgreSQL and then reads what the system actually
answered, stored, logged and audited, because "no plaintext anywhere" cannot be
judged from the response alone.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from harness import (
    CHANGE_PASSWORD_PATH,
    LOGIN_PATH,
    MEMBERS_PATH,
    access_token,
    auth,
    error_code,
    error_details,
    seed_admin,
    seed_tenant,
    seed_user,
    start,
)
from sqlalchemy import select

from app.auth.password import verify_password
from app.logging_conf import configure_logging
from app.models.audit_log import AuditLog
from app.models.refresh_token import RefreshToken
from app.models.user import User

TC_ID = "TC-tech-auth-06"

# One distinct value per write, so a leak can be attributed to the step that
# caused it. Every case checks all three: any password of the case is a password
# that must not be observable.
_CREATED = "first-passphrase-11"
_RESET = "second-passphrase-22"
_CHANGED = "third-passphrase-33"
_EVERY_PASSWORD = (_CREATED, _RESET, _CHANGED)
_SHORT = "tiny-pw"
_WRONG = "wrong-guess-passphrase"
_SPARE = "replacement-passphrase-02"
_EMAIL = "newcomer@acme.com"


@contextmanager
def _captured_logs() -> Iterator[io.StringIO]:
    """Collect the log output the app emits while a case drives it (C-115)."""
    configure_logging(level="INFO", json_output=True)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield stream
    finally:
        root.removeHandler(handler)


def _new_member(password: str) -> dict[str, str]:
    """The local-account creation body every case in this module sends."""
    return {"email": _EMAIL, "name": "New Comer", "password": password}


async def _load_user(session_factory: Any, member_id: str) -> User:
    """Read one member row back, for minting a token or judging its credential."""
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.id == uuid.UUID(member_id)))
    assert user is not None
    return user


def _assert_only_the_hash(user: User, password: str) -> None:
    """The row keeps the slow encoding of ``password`` and no plaintext at all."""
    assert user.password_hash is not None
    assert user.password_hash.startswith("scrypt$")
    assert verify_password(password, user.password_hash)
    for value in _EVERY_PASSWORD:
        assert value not in user.password_hash


def _row_values(row: Any) -> dict[str, str]:
    """Every column of a stored row as text, so one scan covers the whole record."""
    values: dict[str, str] = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        values[column.name] = "" if value is None else str(value)
    return values


@pytest.mark.asyncio
async def test_no_response_carries_a_password_or_a_password_field(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """Create, reset and change answer without the value or a password field (C-115)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        created = await harness.http.post(
            MEMBERS_PATH,
            json=_new_member(_CREATED),
            headers=auth(access_token(admin, tenant)),
        )
        assert created.status_code == 201, created.text
        member_id = created.json()["data"]["id"]

        reset = await harness.http.post(
            f"{MEMBERS_PATH}/{member_id}/password",
            json={"password": _RESET},
            headers=auth(access_token(admin, tenant)),
        )
        assert reset.status_code == 200, reset.text

        member = await _load_user(session_factory, member_id)
        changed = await harness.http.post(
            CHANGE_PASSWORD_PATH,
            json={"currentPassword": _RESET, "newPassword": _CHANGED},
            headers=auth(access_token(member, tenant)),
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["data"]["message"]

        for response in (created, reset, changed):
            assert "passwordHash" not in response.text
            # The only field of these bodies whose name carries the word is the
            # camel-cased forced-change flag; no field is named ``password``.
            assert '"password"' not in response.text
            for password in _EVERY_PASSWORD:
                assert password not in response.text
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_rejected_changes_never_echo_the_passwords(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A 422 and a 401 carry field names and codes only — never a value (F8)."""
    tenant = await seed_tenant(session_factory)
    member = await seed_user(session_factory, tenant, email="member@acme.com", password=_CHANGED)
    harness = await start(session_factory)
    try:
        headers = auth(access_token(member, tenant))

        short = await harness.http.post(
            CHANGE_PASSWORD_PATH,
            json={"currentPassword": _CHANGED, "newPassword": _SHORT},
            headers=headers,
        )
        assert short.status_code == 422, short.text
        assert error_code(short) == "VALIDATION_ERROR"
        assert [detail["loc"][-1] for detail in error_details(short)] == ["newPassword"]
        assert _SHORT not in short.text
        assert _CHANGED not in short.text

        wrong = await harness.http.post(
            CHANGE_PASSWORD_PATH,
            json={"currentPassword": _WRONG, "newPassword": _SPARE},
            headers=headers,
        )
        assert wrong.status_code == 401, wrong.text
        assert error_code(wrong) == "INVALID_CREDENTIALS"
        assert _WRONG not in wrong.text
        assert _SPARE not in wrong.text
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_the_stored_credential_is_only_a_slow_hash(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Every write persists scrypt alone, and the replaced password stops verifying."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        created = await harness.http.post(
            MEMBERS_PATH,
            json=_new_member(_CREATED),
            headers=auth(access_token(admin, tenant)),
        )
        assert created.status_code == 201, created.text
        member_id = created.json()["data"]["id"]

        stored = await _load_user(session_factory, member_id)
        _assert_only_the_hash(stored, _CREATED)
        assert stored.must_change_password is True

        reset = await harness.http.post(
            f"{MEMBERS_PATH}/{member_id}/password",
            json={"password": _RESET},
            headers=auth(access_token(admin, tenant)),
        )
        assert reset.status_code == 200, reset.text
        stored = await _load_user(session_factory, member_id)
        _assert_only_the_hash(stored, _RESET)
        assert stored.password_hash is not None
        assert not verify_password(_CREATED, stored.password_hash)

        changed = await harness.http.post(
            CHANGE_PASSWORD_PATH,
            json={"currentPassword": _RESET, "newPassword": _CHANGED},
            headers=auth(access_token(stored, tenant)),
        )
        assert changed.status_code == 200, changed.text
        stored = await _load_user(session_factory, member_id)
        _assert_only_the_hash(stored, _CHANGED)
        assert stored.password_hash is not None
        assert not verify_password(_RESET, stored.password_hash)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_no_log_line_carries_a_password(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The app's own log output never spells out a value the case supplied (C-115)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        with _captured_logs() as stream:
            created = await harness.http.post(
                MEMBERS_PATH,
                json=_new_member(_CREATED),
                headers=auth(access_token(admin, tenant)),
            )
            assert created.status_code == 201, created.text
            member_id = created.json()["data"]["id"]

            reset = await harness.http.post(
                f"{MEMBERS_PATH}/{member_id}/password",
                json={"password": _RESET},
                headers=auth(access_token(admin, tenant)),
            )
            assert reset.status_code == 200, reset.text

            member = await _load_user(session_factory, member_id)
            changed = await harness.http.post(
                CHANGE_PASSWORD_PATH,
                json={"currentPassword": _RESET, "newPassword": _CHANGED},
                headers=auth(access_token(member, tenant)),
            )
            assert changed.status_code == 200, changed.text

        captured = stream.getvalue()
        # The capture must be real for the absence below to mean anything: every
        # request the case drove leaves one access-log line behind.
        assert "request_completed" in captured
        for password in _EVERY_PASSWORD:
            assert password not in captured
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_no_stored_record_carries_a_password(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Audit rows, and the rows a password write does leave, hold no plaintext (C-115)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        created = await harness.http.post(
            MEMBERS_PATH,
            json=_new_member(_CREATED),
            headers=auth(access_token(admin, tenant)),
        )
        assert created.status_code == 201, created.text
        member_id = created.json()["data"]["id"]
        member = await _load_user(session_factory, member_id)

        # A session first, so the reset and the change have a refresh family to
        # end — the persisted records a password write genuinely produces.
        login = await harness.http.post(LOGIN_PATH, json={"email": _EMAIL, "password": _CREATED})
        assert login.status_code == 200, login.text

        reset = await harness.http.post(
            f"{MEMBERS_PATH}/{member_id}/password",
            json={"password": _RESET},
            headers=auth(access_token(admin, tenant)),
        )
        assert reset.status_code == 200, reset.text

        changed = await harness.http.post(
            CHANGE_PASSWORD_PATH,
            json={"currentPassword": _RESET, "newPassword": _CHANGED},
            headers=auth(access_token(member, tenant)),
        )
        assert changed.status_code == 200, changed.text

        async with session_factory() as session:
            audited = list((await session.execute(select(AuditLog))).scalars())
            revoked = list(
                (
                    await session.execute(
                        select(RefreshToken).where(RefreshToken.user_id == member.id)
                    )
                ).scalars()
            )

        # The reset ended the member's refresh family, so this case really did
        # store rows and the scan below is not an empty one.
        assert revoked
        assert all(token.revoked_reason for token in revoked)

        scanned = [json.dumps(_row_values(row), default=str) for row in [*audited, *revoked]]
        assert len(scanned) == len(audited) + len(revoked)
        for record in scanned:
            for password in _EVERY_PASSWORD:
                assert password not in record
    finally:
        await harness.close()
