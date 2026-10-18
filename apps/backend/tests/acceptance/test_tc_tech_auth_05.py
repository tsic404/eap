"""TC-tech-auth-05 — local account creation, uniqueness and immutable source.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-auth-05 → AC-T15) over
``docs/02-contracts.md`` C-114 / C-28 / C-110. Driven over HTTP against the real
ASGI stack and PostgreSQL; the roster shape is checked where it carries the
provenance these cases assert.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from harness import (
    DEFAULT_DOMAIN,
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
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth.password import verify_password
from app.models.user import ACCOUNT_SOURCE_LOCAL, ACCOUNT_SOURCE_SSO, LOCAL_SUBJECT_PREFIX, User

TC_ID = "TC-tech-auth-05"

_PASSWORD = "handover-passphrase-42"


@pytest.mark.asyncio
async def test_creation_returns_a_local_account_that_must_change(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """201 carries the local provenance and the forced-change marker (C-114)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        response = await harness.http.post(
            MEMBERS_PATH,
            json={"email": "newcomer@acme.com", "name": "New Comer", "password": _PASSWORD},
            headers=auth(access_token(admin, tenant)),
        )
        assert response.status_code == 201, response.text
        created = response.json()["data"]
        assert created["email"] == "newcomer@acme.com"
        assert created["name"] == "New Comer"
        assert created["accountSource"] == ACCOUNT_SOURCE_LOCAL
        assert created["mustChangePassword"] is True
        assert created["status"] == "active"
        assert created["role"] == "employee"
        assert _PASSWORD not in response.text
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_created_account_signs_in_with_the_initial_password(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """The credential the administrator set is real and asks for a change."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        created = await harness.http.post(
            MEMBERS_PATH,
            json={"email": "newcomer@acme.com", "name": "New Comer", "password": _PASSWORD},
            headers=auth(access_token(admin, tenant)),
        )
        assert created.status_code == 201, created.text

        login = await harness.http.post(
            LOGIN_PATH, json={"email": "newcomer@acme.com", "password": _PASSWORD}
        )
        assert login.status_code == 200, login.text
        assert login.json() == {"data": {"mustChangePassword": True}}
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_the_stored_credential_is_only_the_slow_hash(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The row keeps a scrypt encoding under the reserved local subject (C-114)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        await harness.http.post(
            MEMBERS_PATH,
            json={"email": "newcomer@acme.com", "name": "New Comer", "password": _PASSWORD},
            headers=auth(access_token(admin, tenant)),
        )
        async with session_factory() as session:
            stored = await session.scalar(
                select(User).where(User.tenant_id == tenant.id, User.email == "newcomer@acme.com")
            )
        assert stored is not None
        assert stored.password_hash is not None
        assert stored.password_hash.startswith("scrypt$")
        assert _PASSWORD not in stored.password_hash
        assert verify_password(_PASSWORD, stored.password_hash)
        assert stored.sso_sub.startswith(LOCAL_SUBJECT_PREFIX)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_same_address_is_refused_whatever_its_case(session_factory) -> None:  # type: ignore[no-untyped-def]
    """409 ``ACCOUNT_EXISTS`` for the tenant's address, cased or not (C-114)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    await seed_user(session_factory, tenant, email="taken@acme.com", password=_PASSWORD)
    harness = await start(session_factory)
    try:
        for address in ("taken@acme.com", "TAKEN@acme.com"):
            response = await harness.http.post(
                MEMBERS_PATH,
                json={"email": address, "name": "Second", "password": _PASSWORD},
                headers=auth(access_token(admin, tenant)),
            )
            assert response.status_code == 409, response.text
            assert error_code(response) == "ACCOUNT_EXISTS"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_case_variants_of_one_address_cannot_both_land(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Two creates racing on one address, cased differently, leave one row (C-114).

    The lookup folds the address and so does the database's unique key: whoever
    loses the race is answered with the same conflict as a sequential create.
    """
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        responses = await asyncio.gather(
            *(
                harness.http.post(
                    MEMBERS_PATH,
                    json={"email": address, "name": "New Comer", "password": _PASSWORD},
                    headers=auth(access_token(admin, tenant)),
                )
                for address in ("alice@acme.com", "ALICE@acme.com")
            )
        )
        assert sorted(response.status_code for response in responses) == [201, 409]
        refused = next(response for response in responses if response.status_code == 409)
        assert error_code(refused) == "ACCOUNT_EXISTS"

        async with session_factory() as session:
            accounts = (
                await session.scalars(
                    select(User).where(
                        User.tenant_id == tenant.id, func.lower(User.email) == "alice@acme.com"
                    )
                )
            ).all()
        assert len(accounts) == 1
        assert accounts[0].account_source == ACCOUNT_SOURCE_LOCAL

        # The database holds the same line on its own: an address that differs
        # from the stored one only in case is a duplicate there too, so no path
        # can slip a second account past the folded lookup.
        async with session_factory() as session:
            session.add(
                User(
                    tenant_id=tenant.id,
                    sso_sub=f"{LOCAL_SUBJECT_PREFIX}{uuid.uuid4()}",
                    email="aLiCe@acme.com",
                    name="Shadow",
                    role="employee",
                    status="active",
                    account_source=ACCOUNT_SOURCE_LOCAL,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_an_sso_account_blocks_the_same_address_too(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The uniqueness rule covers both sources: no address holds two accounts."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    await seed_user(session_factory, tenant, email="sso-held@acme.com")
    harness = await start(session_factory)
    try:
        response = await harness.http.post(
            MEMBERS_PATH,
            json={"email": "sso-held@acme.com", "name": "Second", "password": _PASSWORD},
            headers=auth(access_token(admin, tenant)),
        )
        assert response.status_code == 409, response.text
        assert error_code(response) == "ACCOUNT_EXISTS"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_email_must_live_inside_the_tenant_domain(session_factory) -> None:  # type: ignore[no-untyped-def]
    """422 when the address is outside the tenant's own domain (C-114)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        response = await harness.http.post(
            MEMBERS_PATH,
            json={"email": "outsider@other.com", "name": "Out Sider", "password": _PASSWORD},
            headers=auth(access_token(admin, tenant)),
        )
        assert response.status_code == 422, response.text
        assert error_code(response) == "VALIDATION_ERROR"
        assert [detail["loc"][-1] for detail in error_details(response)] == ["email"]

        # The domain match folds case, like every other address comparison.
        accepted = await harness.http.post(
            MEMBERS_PATH,
            json={"email": "Mixed@ACME.com", "name": "Mixed", "password": _PASSWORD},
            headers=auth(access_token(admin, tenant)),
        )
        assert accepted.status_code == 201, accepted.text
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_tenant_without_domain_accepts_no_address(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A tenant that declares no domain has no in-domain address (C-114)."""
    tenant = await seed_tenant(session_factory, domain=None)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        response = await harness.http.post(
            MEMBERS_PATH,
            json={"email": f"someone@{DEFAULT_DOMAIN}", "name": "Some One", "password": _PASSWORD},
            headers=auth(access_token(admin, tenant)),
        )
        assert response.status_code == 422, response.text
        assert error_code(response) == "VALIDATION_ERROR"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_missing_fields_name_themselves_without_echoing_values(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """422 lists the unusable fields and never the password they carried (F8)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        response = await harness.http.post(
            MEMBERS_PATH,
            json={"email": "newcomer@acme.com"},
            headers=auth(access_token(admin, tenant)),
        )
        assert response.status_code == 422, response.text
        assert sorted(detail["loc"][-1] for detail in error_details(response)) == [
            "name",
            "password",
        ]

        # An address with no domain, and a blank name, are reported together and
        # the rejected password stays out of both the details and the body.
        blank = await harness.http.post(
            MEMBERS_PATH,
            json={"email": "no-domain", "name": "   ", "password": _PASSWORD},
            headers=auth(access_token(admin, tenant)),
        )
        assert blank.status_code == 422, blank.text
        assert sorted(detail["loc"][-1] for detail in error_details(blank)) == ["email", "name"]
        assert _PASSWORD not in blank.text
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_only_a_platform_administrator_reaches_the_roster(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Every member path answers 403 for the other four roles (C-28)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    member = await seed_user(session_factory, tenant, email="member@acme.com", password=_PASSWORD)
    harness = await start(session_factory)
    try:
        for role in ("agent_admin", "knowledge_admin", "auditor", "employee"):
            outsider = await seed_user(session_factory, tenant, email=f"{role}@acme.com", role=role)
            headers = auth(access_token(outsider, tenant))
            listed = await harness.http.get(MEMBERS_PATH, headers=headers)
            created = await harness.http.post(
                MEMBERS_PATH,
                json={"email": f"x-{role}@acme.com", "name": "X", "password": _PASSWORD},
                headers=headers,
            )
            switched = await harness.http.patch(
                f"{MEMBERS_PATH}/{member.id}", json={"status": "disabled"}, headers=headers
            )
            reset = await harness.http.post(
                f"{MEMBERS_PATH}/{member.id}/password",
                json={"password": _PASSWORD},
                headers=headers,
            )
            for response in (listed, created, switched, reset):
                assert response.status_code == 403, f"{role}: {response.text}"
                assert error_code(response) == "FORBIDDEN"

        # The administrator itself is admitted on all four.
        headers = auth(access_token(admin, tenant))
        assert (await harness.http.get(MEMBERS_PATH, headers=headers)).status_code == 200
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_creation_needs_an_authenticated_administrator(session_factory) -> None:  # type: ignore[no-untyped-def]
    """No account can be created without a platform administrator's token (F7)."""
    tenant = await seed_tenant(session_factory)
    await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        body = {"email": "newcomer@acme.com", "name": "New Comer", "password": _PASSWORD}
        anonymous = await harness.http.post(MEMBERS_PATH, json=body)
        assert anonymous.status_code == 401, anonymous.text
        assert error_code(anonymous) == "UNAUTHORIZED"

        forged = await harness.http.post(
            MEMBERS_PATH, json=body, headers={"Authorization": "Bearer not-a-token"}
        )
        assert forged.status_code == 401, forged.text
        assert error_code(forged) == "UNAUTHORIZED"

        async with session_factory() as session:
            accounts = await session.scalar(
                select(User.id).where(
                    User.tenant_id == tenant.id, User.email == "newcomer@acme.com"
                )
            )
        assert accounts is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_closed_capability_hides_creation_and_reset(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A closed capability answers 404 for create and reset, and keeps the roster."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    member = await seed_user(session_factory, tenant, email="member@acme.com", password=_PASSWORD)
    harness = await start(session_factory, local_login_enabled=False)
    try:
        headers = auth(access_token(admin, tenant))
        created = await harness.http.post(
            MEMBERS_PATH,
            json={"email": "newcomer@acme.com", "name": "New Comer", "password": _PASSWORD},
            headers=headers,
        )
        reset = await harness.http.post(
            f"{MEMBERS_PATH}/{member.id}/password",
            json={"password": _PASSWORD},
            headers=headers,
        )
        for response in (created, reset):
            assert response.status_code == 404, response.text
            assert error_code(response) == "NOT_FOUND"

        # Status changes and the roster itself are not part of the capability.
        listed = await harness.http.get(MEMBERS_PATH, headers=headers)
        assert listed.status_code == 200, listed.text
        switched = await harness.http.patch(
            f"{MEMBERS_PATH}/{member.id}", json={"status": "disabled"}, headers=headers
        )
        assert switched.status_code == 200, switched.text
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_the_roster_exposes_provenance_and_filters(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The page carries entries plus the total, with the C-28 fields and filters."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    await seed_user(
        session_factory,
        tenant,
        email="local@acme.com",
        name="Local One",
        password=_PASSWORD,
        must_change_password=True,
    )
    await seed_user(
        session_factory,
        tenant,
        email="sso@acme.com",
        name="Sso One",
        role="auditor",
        status="disabled",
    )
    harness = await start(session_factory)
    try:
        headers = auth(access_token(admin, tenant))
        page = await harness.http.get(MEMBERS_PATH, headers=headers)
        assert page.status_code == 200, page.text
        body = page.json()["data"]
        assert body["total"] == 3
        assert len(body["items"]) == 3
        by_email = {item["email"]: item for item in body["items"]}
        assert by_email["local@acme.com"]["accountSource"] == ACCOUNT_SOURCE_LOCAL
        assert by_email["local@acme.com"]["mustChangePassword"] is True
        assert by_email["sso@acme.com"]["accountSource"] == ACCOUNT_SOURCE_SSO
        assert by_email["sso@acme.com"]["mustChangePassword"] is False
        assert set(by_email["local@acme.com"]) == {
            "id",
            "email",
            "name",
            "role",
            "status",
            "department",
            "avatarText",
            "lastActiveAt",
            "createdAt",
            "accountSource",
            "mustChangePassword",
        }

        searched = await harness.http.get(MEMBERS_PATH, params={"search": "sso@"}, headers=headers)
        assert [item["email"] for item in searched.json()["data"]["items"]] == ["sso@acme.com"]
        assert searched.json()["data"]["total"] == 1

        by_status = await harness.http.get(
            MEMBERS_PATH, params={"status": "disabled"}, headers=headers
        )
        assert [item["email"] for item in by_status.json()["data"]["items"]] == ["sso@acme.com"]

        by_role = await harness.http.get(
            MEMBERS_PATH, params={"role": "platform_admin"}, headers=headers
        )
        assert [item["email"] for item in by_role.json()["data"]["items"]] == ["admin@acme.com"]

        sized = await harness.http.get(
            MEMBERS_PATH, params={"page": 2, "pageSize": 2}, headers=headers
        )
        assert sized.json()["data"]["total"] == 3
        assert len(sized.json()["data"]["items"]) == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_another_tenants_roster_is_invisible(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A tenant's roster holds only its own members (C-28 / F1)."""
    tenant = await seed_tenant(session_factory)
    other = await seed_tenant(session_factory, domain="other.com", name="Other")
    admin = await seed_admin(session_factory, tenant)
    await seed_user(session_factory, other, email="foreign@other.com", password=_PASSWORD)
    harness = await start(session_factory)
    try:
        page = await harness.http.get(MEMBERS_PATH, headers=auth(access_token(admin, tenant)))
        assert page.status_code == 200, page.text
        assert [item["email"] for item in page.json()["data"]["items"]] == ["admin@acme.com"]
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_member_detail_reads_one_account_and_hides_foreign_ones(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """A single-member read carries the same fields, and a foreign id is absent."""
    tenant = await seed_tenant(session_factory)
    other = await seed_tenant(session_factory, domain="other.com", name="Other")
    admin = await seed_admin(session_factory, tenant)
    member = await seed_user(
        session_factory, tenant, email="local@acme.com", password=_PASSWORD, department="研发"
    )
    foreign = await seed_user(session_factory, other, email="foreign@other.com", password=_PASSWORD)
    harness = await start(session_factory)
    try:
        headers = auth(access_token(admin, tenant))
        detail = await harness.http.get(f"{MEMBERS_PATH}/{member.id}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["email"] == "local@acme.com"
        assert detail.json()["data"]["department"] == "研发"
        assert detail.json()["data"]["accountSource"] == ACCOUNT_SOURCE_LOCAL

        for foreign_id in (foreign.id, uuid.uuid4(), "not-a-uuid"):
            response = await harness.http.get(f"{MEMBERS_PATH}/{foreign_id}", headers=headers)
            assert response.status_code == 404, response.text
            assert error_code(response) == "NOT_FOUND"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_the_source_is_not_a_writable_input(session_factory) -> None:  # type: ignore[no-untyped-def]
    """No request can set the provenance, and the database refuses a switch (C-114)."""
    tenant = await seed_tenant(session_factory)
    admin = await seed_admin(session_factory, tenant)
    harness = await start(session_factory)
    try:
        headers = auth(access_token(admin, tenant))
        ignored = await harness.http.post(
            MEMBERS_PATH,
            json={
                "email": "newcomer@acme.com",
                "name": "New Comer",
                "password": _PASSWORD,
                "accountSource": ACCOUNT_SOURCE_SSO,
            },
            headers=headers,
        )
        assert ignored.status_code == 422, ignored.text
        assert [detail["loc"][-1] for detail in error_details(ignored)] == ["accountSource"]

        created = await harness.http.post(
            MEMBERS_PATH,
            json={"email": "newcomer@acme.com", "name": "New Comer", "password": _PASSWORD},
            headers=headers,
        )
        assert created.status_code == 201, created.text

        async with session_factory() as session:
            stored = await session.scalar(
                select(User.id).where(
                    User.tenant_id == tenant.id, User.email == "newcomer@acme.com"
                )
            )
        assert stored is not None
        # The row keeps its provenance even as its other columns change.
        await harness.http.patch(
            f"{MEMBERS_PATH}/{stored}", json={"status": "disabled"}, headers=headers
        )
        await harness.http.post(
            f"{MEMBERS_PATH}/{stored}/password",
            json={"password": "second-passphrase-9"},
            headers=headers,
        )
        async with session_factory() as session:
            after = await session.scalar(select(User).where(User.id == stored))
        assert after is not None
        assert after.account_source == ACCOUNT_SOURCE_LOCAL
        assert after.must_change_password is True

        # The schema refuses the switch even for a direct write, so no future
        # code path can flip an account's provenance behind the contract.
        async with session_factory() as session:
            row = await session.scalar(select(User).where(User.id == stored))
            assert row is not None
            row.account_source = ACCOUNT_SOURCE_SSO
            with pytest.raises(IntegrityError):
                await session.commit()
    finally:
        await harness.close()
