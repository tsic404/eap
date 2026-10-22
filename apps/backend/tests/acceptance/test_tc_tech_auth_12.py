"""TC-tech-auth-12 — tenant-domain fold equivalence classes on both login paths.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-auth-12 → AC-T21) over
``docs/02-contracts.md`` C-122 / C-23 / C-111. One spelling of a domain is
driven through the local login endpoint and through the enterprise callback
(fake identity provider), so the same equivalence class must reach the same
tenant — or the same ``TENANT_*`` code — on both paths.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

import httpx
import pytest
from harness import (
    CALLBACK_PATH,
    FRONTEND_HOME,
    FRONTEND_LOGIN,
    LOGIN_PATH,
    FakeIdp,
    begin_login,
    error_code,
    seed_tenant,
    seed_user,
    start,
)
from sqlalchemy import select

from app.models.user import User

TC_ID = "TC-tech-auth-12"

_PASSWORD = "acceptance-passphrase-12"
# ``İ`` folds to ``i`` plus U+0307 COMBINING DOT ABOVE, not to plain ``i``.
_DOTTED_CAPITAL_I = "i\u0307stanbul.de"
# ``Σ`` at the end of the string folds to final sigma; inside it stays ``σ``.
_FINAL_SIGMA = "example.οδος"
_INNER_SIGMA = "example.οδοσ"
# ``ΟΔΟΣ.DE`` keeps a letter after the sigma, so the sigma stays non-final.
_CAPS_SIGMA = "ΟΔΟΣ.DE"
_NON_FINAL_SIGMA = "οδοσ.DE"
_FINAL_SIGMA_DOMAIN = "οδος.DE"
# Precomposed and decomposed ``é``: distinct, because nothing is normalised.
_PRECOMPOSED = "café.de"
_DECOMPOSED = "cafe\u0301.de"
# Full-width and punycode spellings are not the ASCII domain.
_FULLWIDTH = "ｅｘａｍｐｌｅ.de"
_PUNYCODE = "xn--strae-oqa.de"

_FOLD_EQUAL = [
    ("example.de", "EXAMPLE.DE"),
    ("straße.de", "STRAẞE.DE"),
    ("İSTANBUL.DE", _DOTTED_CAPITAL_I),
    ("example.ΟΔΟΣ", _FINAL_SIGMA),
    (_CAPS_SIGMA, _NON_FINAL_SIGMA),
]

_FOLD_DISTINCT = [
    ("straße.de", "strasse.de"),
    ("İSTANBUL.DE", "istanbul.de"),
    ("example.ΟΔΟΣ", _INNER_SIGMA),
    (_CAPS_SIGMA, _FINAL_SIGMA_DOMAIN),
    ("ırmak.de", "irmak.de"),
    ("example.de", _FULLWIDTH),
    ("straße.de", _PUNYCODE),
    (_PRECOMPOSED, _DECOMPOSED),
]


async def _local_login(harness: Any, email: str) -> httpx.Response:
    return await harness.http.post(LOGIN_PATH, json={"email": email, "password": _PASSWORD})


async def _callback(harness: Any, idp: FakeIdp, email: str) -> httpx.Response:
    """Answer the enterprise callback for an identity carrying ``email``."""
    idp.email = email
    state = await begin_login(harness.http, idp)
    return await harness.http.get(
        CALLBACK_PATH, params={"code": "authorization-code", "state": state}, follow_redirects=False
    )


def _assert_login_error(response: httpx.Response, code: str) -> None:
    assert response.status_code == 302, response.text
    target = urllib.parse.urlparse(response.headers.get("location", ""))
    assert target.path == FRONTEND_LOGIN
    errors = urllib.parse.parse_qs(target.query).get("error", [])
    assert errors == [code], response.headers.get("location")


async def _sso_user_tenant(session_factory: Any, email: str) -> Any:
    async with session_factory() as session:
        return await session.scalar(select(User.tenant_id).where(User.email == email))


@pytest.mark.asyncio
@pytest.mark.parametrize(("stored", "variant"), _FOLD_EQUAL)
async def test_one_fold_class_reaches_one_tenant_on_both_paths(
    session_factory, stored: str, variant: str
) -> None:  # type: ignore[no-untyped-def]
    """A fold-equal spelling resolves to the tenant on local login and on the callback."""
    tenant = await seed_tenant(session_factory, domain=stored, name="Fold Class")
    await seed_user(session_factory, tenant, email=f"admin@{stored}", password=_PASSWORD)

    idp = FakeIdp(sub=f"sub-{variant}", email=f"sso@{variant}")
    harness = await start(session_factory, idp=idp)
    try:
        local = await _local_login(harness, f"admin@{variant}")
        assert local.status_code == 200, (variant, local.text)

        callback = await _callback(harness, idp, f"sso@{variant}")
        assert callback.status_code == 302, callback.text
        assert callback.headers["location"] == FRONTEND_HOME, (variant, callback.headers)
        assert await _sso_user_tenant(session_factory, f"sso@{variant}") == tenant.id
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("stored", "other"), _FOLD_DISTINCT)
async def test_fold_distinct_spelling_reaches_no_tenant_on_either_path(
    session_factory, stored: str, other: str
) -> None:  # type: ignore[no-untyped-def]
    """A spelling outside the class answers the same TENANT_NOT_FOUND on both paths."""
    tenant = await seed_tenant(session_factory, domain=stored, name="Fold Class")
    await seed_user(session_factory, tenant, email=f"admin@{stored}", password=_PASSWORD)

    idp = FakeIdp(sub=f"sub-{other}", email=f"sso@{other}")
    harness = await start(session_factory, idp=idp)
    try:
        local = await _local_login(harness, f"admin@{other}")
        assert local.status_code == 403, (other, local.text)
        assert error_code(local) == "TENANT_NOT_FOUND"

        callback = await _callback(harness, idp, f"sso@{other}")
        _assert_login_error(callback, "TENANT_NOT_FOUND")
        assert await _sso_user_tenant(session_factory, f"sso@{other}") is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_two_fold_equal_tenants_answer_the_same_ambiguity_on_both_paths(
    session_factory,
) -> None:  # type: ignore[no-untyped-def]
    """C-23 / C-111 answer TENANT_AMBIGUOUS alike when two stored domains fold together."""
    await seed_tenant(session_factory, domain="EXAMPLE.DE", name="Example One")
    await seed_tenant(session_factory, domain="example.de", name="Example Two")

    idp = FakeIdp(sub="sub-ambiguous", email="sso@Example.De")
    harness = await start(session_factory, idp=idp)
    try:
        local = await _local_login(harness, "admin@Example.De")
        assert local.status_code == 403, local.text
        assert error_code(local) == "TENANT_AMBIGUOUS"

        callback = await _callback(harness, idp, "sso@Example.De")
        _assert_login_error(callback, "TENANT_AMBIGUOUS")
        assert await _sso_user_tenant(session_factory, "sso@Example.De") is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_local_part_case_does_not_change_the_tenant(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Only the domain resolves the tenant; the address itself still matches one account."""
    tenant = await seed_tenant(session_factory, domain="example.de", name="Example")
    await seed_user(session_factory, tenant, email="admin@example.de", password=_PASSWORD)

    harness = await start(session_factory)
    try:
        for email in ("admin@example.de", "ADMIN@example.de", "Admin@EXAMPLE.de"):
            login = await _local_login(harness, email)
            assert login.status_code == 200, (email, login.text)
    finally:
        await harness.close()
