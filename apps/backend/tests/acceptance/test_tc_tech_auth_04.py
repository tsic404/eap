"""TC-tech-auth-04 — local login judgement order, error semantics and lockout.

Scenario semantics: ``docs/03-acceptance.md`` (TC-tech-auth-04 → AC-T14) over
``docs/02-contracts.md`` C-110 ~ C-112, reusing C-2 / C-3 / C-10. Every step is
exercised over HTTP against the real ASGI stack and PostgreSQL; the lockout clock
is driven by hand, so the 15-minute window is asserted rather than waited for.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.local_login import LocalLoginService
from app.auth.login_lockout import InMemoryLoginAttempts
from app.auth.password import hash_password
from app.auth.refresh import RefreshService
from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models.tenant import Tenant
from app.models.user import ACCOUNT_SOURCE_LOCAL, User
from app.rate_limit import LOCAL_LOGIN_PATH

# The imports above must precede every other statement; the case identifier
# leads the module body from here on.
TC_ID = "TC-tech-auth-04"

LOGIN_PATH = LOCAL_LOGIN_PATH
CAPABILITY_PATH = "/api/auth/capabilities"
OIDC_LOGIN_PATH = "/api/auth/login"

_PASSWORD = "correct-horse-battery-staple"
_EMAIL = "alice@acme.com"
_DOMAIN = "acme.com"
# C-112's own numbers: five consecutive failures, a fifteen-minute window. The
# store is left at its defaults so these cases pin the shipped policy.
_MAX_FAILURES = 5
_LOCK_SECONDS = 900
# High enough that only the rate-limit case runs into the login budget.
_ROOMY_LOGIN_LIMIT = 100
_TIGHT_LOGIN_LIMIT = 5

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_PEM = (
    _KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)
_PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()


class _Clock:
    """Manually advanced clock, so a lock window is asserted, not waited for."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _unique_client_ip() -> str:
    """A fresh loopback-adjacent address per app, so login buckets never mix."""
    suffix = uuid.uuid4().int
    return f"10.{(suffix >> 16) & 0xFF}.{(suffix >> 8) & 0xFF}.{suffix & 0xFF}"


class Harness:
    """A running app plus the handles a case needs to steer it."""

    def __init__(self, http: httpx.AsyncClient, clock: _Clock) -> None:
        self.http = http
        self.clock = clock

    async def login(
        self, *, email: object = _EMAIL, password: object = _PASSWORD
    ) -> httpx.Response:
        return await self.http.post(LOGIN_PATH, json={"email": email, "password": password})

    async def close(self) -> None:
        await self.http.aclose()


async def _start(
    session_factory,
    *,
    local_login_enabled: bool = True,
    login_per_minute: int = _ROOMY_LOGIN_LIMIT,
) -> Harness:
    """Boot the app wired to an in-memory lockout whose clock the case owns.

    The service shares the app's rate limiter with the middleware, so the login
    budget is the single count both login paths draw on (C-10).
    """
    clock = _Clock()
    settings = Settings(
        _env_file=None,
        local_login_enabled=local_login_enabled,
        rate_limit_login_per_minute=login_per_minute,
        jwt_private_key=_PRIVATE_PEM,
        jwt_public_key=_PUBLIC_PEM,
    )
    app = create_app(settings)
    app.state.refresh_service = RefreshService(settings)
    app.state.local_login_service = LocalLoginService(
        settings,
        limiter=app.state.rate_limiter,
        attempts=InMemoryLoginAttempts(clock=clock),
    )

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    # Each case gets its own client IP: the login bucket lives in the shared
    # store when Redis is up, and a private address keeps cases (and the rest of
    # the suite) from spending each other's budget.
    transport = httpx.ASGITransport(
        app=app, raise_app_exceptions=False, client=(_unique_client_ip(), 1234)
    )
    return Harness(
        httpx.AsyncClient(transport=transport, base_url="http://test"),
        clock,
    )


async def _seed_tenant(session_factory, *, domain: str = _DOMAIN, name: str = "Acme") -> Tenant:
    async with session_factory() as session:
        tenant = Tenant(name=name, slug=f"{name.lower()}-{domain.lower()}", sso_domain=domain)
        session.add(tenant)
        await session.commit()
        return tenant


async def _seed_user(
    session_factory,
    tenant: Tenant,
    *,
    email: str = _EMAIL,
    password: str = _PASSWORD,
    status: str = "active",
    must_change_password: bool = False,
) -> User:
    async with session_factory() as session:
        user = User(
            tenant_id=tenant.id,
            sso_sub=f"local:{email}",
            email=email,
            name="Alice",
            role="employee",
            status=status,
            password_hash=hash_password(password),
            must_change_password=must_change_password,
            account_source=ACCOUNT_SOURCE_LOCAL,
        )
        session.add(user)
        await session.commit()
        return user


def _error_code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


def _cookie_attributes(response: httpx.Response) -> dict[str, str]:
    """Parse the response's ``Set-Cookie`` header into a lowercased attribute map."""
    header = response.headers.get("set-cookie")
    assert header, "expected a Set-Cookie header"
    segments = [segment.strip() for segment in header.split(";")]
    name, _, value = segments[0].partition("=")
    attributes = {"name": name, "value": value}
    for segment in segments[1:]:
        key, separator, attribute = segment.partition("=")
        attributes[key.lower()] = attribute if separator else "true"
    return attributes


# ── ① capability switch and the public read point (C-110) ───────────────────


@pytest.mark.asyncio
async def test_capability_read_point_reports_both_states(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The read point needs no authentication and answers in both directions."""
    on = await _start(session_factory, local_login_enabled=True)
    off = await _start(session_factory, local_login_enabled=False)
    try:
        enabled = await on.http.get(CAPABILITY_PATH)
        assert enabled.status_code == 200, enabled.text
        assert enabled.json() == {"data": {"localLoginEnabled": True}}

        disabled = await off.http.get(CAPABILITY_PATH)
        assert disabled.status_code == 200, disabled.text
        assert disabled.json() == {"data": {"localLoginEnabled": False}}
    finally:
        await on.close()
        await off.close()


def test_capability_read_point_defaults_to_disabled() -> None:
    """A deployment that never declares the capability reads as disabled (NG4)."""
    settings = Settings(_env_file=None)
    assert settings.local_login_enabled is False


@pytest.mark.asyncio
async def test_disabled_capability_answers_404(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Login on a closed capability is a non-existent path, not a rejected login."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    harness = await _start(session_factory, local_login_enabled=False)
    try:
        response = await harness.login()
        assert response.status_code == 404, response.text
        assert _error_code(response) == "NOT_FOUND"
        assert "set-cookie" not in response.headers
        # The same envelope a path the framework does not know answers with, so
        # a closed capability reads as a missing path and not as a refusal.
        unknown = await harness.http.post(f"{LOGIN_PATH}/unknown", json={})
        assert unknown.status_code == 404, unknown.text
        assert response.json() == unknown.json()
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_capability_is_judged_before_the_login_limit(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Closed capability wins over an exhausted login budget (C-111 ① before ②)."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    harness = await _start(
        session_factory, local_login_enabled=False, login_per_minute=_TIGHT_LOGIN_LIMIT
    )
    try:
        # The OIDC start consumes the same login budget, so this drains it
        # without depending on the local path at all.
        for _ in range(_TIGHT_LOGIN_LIMIT):
            await harness.http.get(OIDC_LOGIN_PATH, follow_redirects=False)

        response = await harness.login()
        assert response.status_code == 404, response.text
        assert _error_code(response) == "NOT_FOUND"
    finally:
        await harness.close()


# ── ② client-IP login limit (C-10) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_requests_are_limited_per_client(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The login budget is shared by both login paths and answered with RATE_LIMITED."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    harness = await _start(session_factory, login_per_minute=_TIGHT_LOGIN_LIMIT)
    try:
        for _ in range(_TIGHT_LOGIN_LIMIT):
            allowed = await harness.login()
            assert allowed.status_code == 200, allowed.text

        blocked = await harness.login()
        assert blocked.status_code == 429, blocked.text
        assert _error_code(blocked) == "RATE_LIMITED"

        # Same bucket as the enterprise-identity start: that path is over its
        # budget too, before any OIDC configuration is consulted.
        oidc = await harness.http.get(OIDC_LOGIN_PATH, follow_redirects=False)
        assert oidc.status_code == 429, oidc.text
        assert _error_code(oidc) == "RATE_LIMITED"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_login_limit_is_judged_before_the_request_fields(session_factory) -> None:  # type: ignore[no-untyped-def]
    """An empty body is charged to the login budget first and then refused (③)."""
    housing = await _start(session_factory, login_per_minute=_TIGHT_LOGIN_LIMIT)
    try:
        for _ in range(_TIGHT_LOGIN_LIMIT):
            await housing.http.post(LOGIN_PATH, json={})

        response = await housing.http.post(LOGIN_PATH, json={})
        assert response.status_code == 429, response.text
        assert _error_code(response) == "RATE_LIMITED"
    finally:
        await housing.close()


@pytest.mark.asyncio
async def test_a_redirected_login_spelling_costs_one_token(session_factory) -> None:  # type: ignore[no-untyped-def]
    """The non-canonical spelling is redirected, not charged again (C-10)."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    harness = await _start(session_factory, login_per_minute=_TIGHT_LOGIN_LIMIT)
    try:
        # The framework redirects the slash-suffixed spelling to the route; only
        # the request that reaches the route may spend the login budget.
        for _ in range(_TIGHT_LOGIN_LIMIT):
            redirect = await harness.http.post(
                f"{LOGIN_PATH}/", json={}, follow_redirects=False
            )
            assert redirect.status_code == 307, redirect.text

        allowed = await harness.login()
        assert allowed.status_code == 200, allowed.text
    finally:
        await harness.close()


# ── ③ request fields ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_fields_are_rejected_per_field(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Missing or unusable fields answer 422 with the offending fields named."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    harness = await _start(session_factory)
    try:
        cases: list[dict[str, object]] = [
            {},
            {"email": _EMAIL},
            {"password": _PASSWORD},
            {"email": "   ", "password": _PASSWORD},
            {"email": _EMAIL, "password": ""},
            {"email": 42, "password": _PASSWORD},
        ]
        for payload in cases:
            response = await harness.http.post(LOGIN_PATH, json=payload)
            assert response.status_code == 422, (payload, response.text)
            error = response.json()["error"]
            assert error["code"] == "VALIDATION_ERROR"
            assert isinstance(error["details"], list) and error["details"]
            assert "set-cookie" not in response.headers
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_no_body_at_all_is_rejected_after_the_limit(session_factory) -> None:  # type: ignore[no-untyped-def]
    """An absent body is the same 422 as an empty one, never a framework 500."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    harness = await _start(session_factory)
    try:
        response = await harness.http.post(LOGIN_PATH)
        assert response.status_code == 422, response.text
        assert _error_code(response) == "VALIDATION_ERROR"
    finally:
        await harness.close()


# ── ④ tenant resolution (C-111 ④, same rules as C-23) ──────────────────────


@pytest.mark.asyncio
async def test_tenant_must_match_the_email_domain(session_factory) -> None:  # type: ignore[no-untyped-def]
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    harness = await _start(session_factory)
    try:
        response = await harness.login(email="bob@other.example", password=_PASSWORD)
        assert response.status_code == 403, response.text
        assert _error_code(response) == "TENANT_NOT_FOUND"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_case_variant_domains_are_ambiguous(session_factory) -> None:  # type: ignore[no-untyped-def]
    tenant = await _seed_tenant(session_factory, domain="acme.com", name="Acme")
    await _seed_user(session_factory, tenant)
    await _seed_tenant(session_factory, domain="Acme.com", name="AcmeTwo")
    harness = await _start(session_factory)
    try:
        response = await harness.login(email="bob@acme.com", password=_PASSWORD)
        assert response.status_code == 403, response.text
        assert _error_code(response) == "TENANT_AMBIGUOUS"
    finally:
        await harness.close()


# ── ⑤ failure lockout (C-112) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_five_failures_lock_the_identifier_for_fifteen_minutes(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Five consecutive failures lock the identifier; the lock outlives the attempts."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    harness = await _start(session_factory)
    try:
        for _ in range(_MAX_FAILURES):
            failed = await harness.login(password="wrong-password")
            assert failed.status_code == 401, failed.text

        # Locked: the correct password is not even verified any more.
        locked = await harness.login()
        assert locked.status_code == 429, locked.text
        assert _error_code(locked) == "LOGIN_LOCKED"
        assert "set-cookie" not in locked.headers

        harness.clock.advance(_LOCK_SECONDS - 1)
        still_locked = await harness.login()
        assert still_locked.status_code == 429, still_locked.text

        harness.clock.advance(1)
        released = await harness.login()
        assert released.status_code == 200, released.text
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_lockout_counts_identifiers_without_an_account(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Failures are counted per (tenant, email) whether or not that email exists."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    harness = await _start(session_factory)
    try:
        for _ in range(_MAX_FAILURES):
            ghost = await harness.login(email="ghost@acme.com", password="whatever")
            assert ghost.status_code == 401, ghost.text

        ghost_locked = await harness.login(email="ghost@acme.com", password="whatever")
        assert ghost_locked.status_code == 429, ghost_locked.text
        assert _error_code(ghost_locked) == "LOGIN_LOCKED"

        # One identifier's lock does not spill into another's.
        other = await harness.login()
        assert other.status_code == 200, other.text
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_successful_login_clears_the_failures(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A success drops the count, so the next streak starts from zero."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    harness = await _start(session_factory)
    try:
        for _ in range(_MAX_FAILURES - 1):
            await harness.login(password="wrong-password")

        cleared = await harness.login()
        assert cleared.status_code == 200, cleared.text

        for _ in range(_MAX_FAILURES - 1):
            failed = await harness.login(password="wrong-password")
            assert failed.status_code == 401, failed.text

        # Four failures after the clear are still four: the count began at zero.
        still_allowed = await harness.login()
        assert still_allowed.status_code == 200, still_allowed.text
    finally:
        await harness.close()


# ── ⑥ credentials: one indistinguishable answer (C-111 ⑥) ──────────────────


@pytest.mark.asyncio
async def test_unknown_wrong_and_disabled_are_indistinguishable(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Unknown account, wrong password and a disabled account share one answer."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    await _seed_user(session_factory, tenant, email="bob@acme.com", status="disabled")
    harness = await _start(session_factory)
    try:
        unknown = await harness.login(email="nobody@acme.com", password=_PASSWORD)
        wrong = await harness.login(password="wrong-password")
        disabled = await harness.login(email="bob@acme.com", password=_PASSWORD)

        for response in (unknown, wrong, disabled):
            assert response.status_code == 401, response.text
            assert _error_code(response) == "INVALID_CREDENTIALS"
            assert "set-cookie" not in response.headers
        assert unknown.json() == wrong.json() == disabled.json()
        # The refused password never travels back (F8).
        assert _PASSWORD not in unknown.text
    finally:
        await harness.close()


# ── success shape: cookie only, no access token, no password (C-2 / C-3) ────


@pytest.mark.asyncio
async def test_success_returns_only_the_change_flag_and_the_refresh_cookie(  # type: ignore[no-untyped-def]
    session_factory,
) -> None:
    """200 carries one datum plus the C-3 cookie; the access token stays on refresh."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    harness = await _start(session_factory)
    try:
        response = await harness.login()
        assert response.status_code == 200, response.text
        assert response.json() == {"data": {"mustChangePassword": False}}
        assert "accessToken" not in response.text
        assert _PASSWORD not in response.text

        cookie = _cookie_attributes(response)
        assert cookie["name"] == "refresh_token"
        assert cookie["value"]
        assert cookie["httponly"] == "true"
        assert cookie["path"] == "/api/auth"
        assert cookie["samesite"] == "lax"
        assert cookie["max-age"] == "2592000"

        # The browser only ever gets an access token through the refresh channel.
        refreshed = await harness.http.post(
            "/api/auth/refresh", headers={"Cookie": f"refresh_token={cookie['value']}"}
        )
        assert refreshed.status_code == 200, refreshed.text
        access_token = refreshed.json()["data"]["accessToken"]

        me = await harness.http.get("/api/me", headers={"Authorization": f"Bearer {access_token}"})
        assert me.status_code == 200, me.text
        assert me.json()["data"]["email"] == _EMAIL
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_success_reports_the_forced_change_flag(session_factory) -> None:  # type: ignore[no-untyped-def]
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant, must_change_password=True)
    harness = await _start(session_factory)
    try:
        response = await harness.login()
        assert response.status_code == 200, response.text
        assert response.json() == {"data": {"mustChangePassword": True}}
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_email_case_does_not_change_the_identifier(session_factory) -> None:  # type: ignore[no-untyped-def]
    """A case-variant address reaches the same account and the same lockout."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    harness = await _start(session_factory)
    try:
        upper = await harness.login(email="ALICE@ACME.COM")
        assert upper.status_code == 200, upper.text

        for _ in range(_MAX_FAILURES):
            await harness.login(email="Alice@Acme.com", password="wrong-password")

        locked = await harness.login(email=_EMAIL)
        assert locked.status_code == 429, locked.text
        assert _error_code(locked) == "LOGIN_LOCKED"
    finally:
        await harness.close()


# ── closed state keeps existing sessions usable (C-110 / AC-46) ─────────────


@pytest.mark.asyncio
async def test_disabled_capability_keeps_existing_sessions_alive(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Closing the capability stops the login path, not the sessions it issued."""
    tenant = await _seed_tenant(session_factory)
    await _seed_user(session_factory, tenant)
    enabled = await _start(session_factory, local_login_enabled=True)
    disabled = await _start(session_factory, local_login_enabled=False)
    try:
        session = await enabled.login()
        assert session.status_code == 200, session.text
        refresh_cookie = _cookie_attributes(session)["value"]

        closed_login = await disabled.login()
        assert closed_login.status_code == 404, closed_login.text

        survived = await disabled.http.post(
            "/api/auth/refresh",
            headers={"Cookie": f"refresh_token={refresh_cookie}"},
        )
        assert survived.status_code == 200, survived.text
        assert survived.json()["data"]["accessToken"]
    finally:
        await enabled.close()
        await disabled.close()
