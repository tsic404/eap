"""Acceptance case TC-tech-upload-03.

Chat attachments and knowledge-base documents spend one upload budget: ten per
user per minute over either endpoint, the eleventh refused with 429
``RATE_LIMITED`` and no rate-limit headers. Listing the documents is not an
upload and spends nothing, and one user's spent budget leaves another's intact.

The limiter runs against the shared store with a clock the case advances.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.rate_limit import _TOKEN_BUCKET_LUA, RedisTokenBucket
from app.services.knowledge import KnowledgeService
from tests.conftest import default_console

TC_ID = "TC-tech-upload-03"

_ATTACHMENT_PATH = "/api/files/upload"
_KB_ID = "kb-1"
_DOCUMENTS_PATH = f"/api/knowledge-bases/{_KB_ID}/documents"
_LIMIT = 10
_PDF = b"%PDF-1.7 acceptance"


class _Clock:
    """A wall clock the case advances by hand."""

    def __init__(self) -> None:
        self.now = time.time()

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _clock_driven_script() -> str:
    """The production bucket script, with its ``TIME`` read taken from an argument.

    Production reads the current time from Redis itself so workers with skewed
    clocks cannot distort the shared bucket; a test cannot advance that clock,
    so the same script runs with the timestamp supplied by ``_Clock``. The
    atomic check-and-consume, the two keys and the TTL stay production behaviour.
    """
    injected = _TOKEN_BUCKET_LUA.replace(
        "local now = redis.call('TIME')\n"
        "local now_seconds = tonumber(now[1]) + tonumber(now[2]) / 1000000",
        "local now_seconds = tonumber(ARGV[3])",
    )
    if injected == _TOKEN_BUCKET_LUA:
        raise AssertionError(
            "the token-bucket script no longer reads its clock through redis.call('TIME')"
        )
    return injected


class _ClockBucket(RedisTokenBucket):
    """The real Redis token bucket, refilling on the test's clock."""

    def __init__(self, redis_url: str, clock: _Clock) -> None:
        super().__init__(redis_url)
        self._clock = clock
        self._script = _clock_driven_script()

    async def _eval(self, key: str, capacity: int, refill_per_second: float) -> bool:
        result = await self._redis.eval(
            self._script,
            2,
            key,
            f"{key}:ts",
            capacity,
            refill_per_second,
            self._clock.now,
        )
        return bool(result)


def _console() -> Any:
    """The Dify boundary stand-in, issuing a distinct document id per upload."""
    console = default_console()
    issued = {"n": 0}

    async def _create_document(*_args: object, **_kwargs: object) -> dict[str, Any]:
        issued["n"] += 1
        return {"documents": [{"id": f"doc-{issued['n']}", "indexing_status": "indexing"}]}

    console.create_document.side_effect = _create_document
    return console


@asynccontextmanager
async def _rate_limited_client(
    api: Any, monkeypatch: pytest.MonkeyPatch, clock: _Clock
) -> AsyncIterator[httpx.AsyncClient]:
    """An app with rate limiting enabled over the fixture's database and Redis.

    The limiter is constructed with ``clock``, so refill is decided by the case
    and never by request latency. Every construction gets a bucket of its own,
    so a fallback counter is never shared between cases.
    """
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=api.settings.database_url,
        redis_url=api.settings.redis_url,
        jwt_public_key=api.settings.jwt_public_key,
        rate_limit_enabled=True,
        # The global per-IP bucket is widened so it can never mask the per-user
        # upload rule this case is about.
        rate_limit_requests=100_000,
    )
    monkeypatch.setattr(
        "app.main.RedisTokenBucket",
        lambda *args, **kwargs: _ClockBucket(api.settings.redis_url, clock),
    )
    app = create_app(settings)
    # The lifespan does not run under an ASGI transport, so wire the state the
    # routers resolve.
    console = _console()
    app.state.dify_console = console
    app.state.knowledge_service = KnowledgeService(console, settings=settings)

    async def override_get_session() -> AsyncIterator[Any]:
        async with api.db.session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    # Every case uses its own synthetic client address: the global per-IP bucket
    # is shared Redis state.
    octets = uuid.uuid4().int.to_bytes(16, "big")[-2:]
    transport = httpx.ASGITransport(
        app=app, raise_app_exceptions=False, client=(f"10.43.{octets[0]}.{octets[1]}", 12345)
    )
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        await app.state.rate_limiter.aclose()


async def _upload_attachment(client: httpx.AsyncClient, headers: dict[str, str]) -> httpx.Response:
    """One chat attachment upload (C-78)."""
    return await client.post(
        _ATTACHMENT_PATH,
        headers=headers,
        files={"file": ("notes.txt", b"acceptance", "text/plain")},
    )


async def _upload_document(client: httpx.AsyncClient, headers: dict[str, str]) -> httpx.Response:
    """One knowledge-base document upload."""
    return await client.post(
        _DOCUMENTS_PATH,
        headers=headers,
        files={"file": ("report.pdf", _PDF, "application/pdf")},
    )


def _assert_rate_limited(response: httpx.Response) -> None:
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    # C-10: the refusal carries no rate-limit metadata.
    names = {name.lower() for name in response.headers}
    assert "retry-after" not in names
    assert not any(name.startswith("x-ratelimit") for name in names)


@pytest.mark.asyncio
async def test_ten_mixed_uploads_pass_and_the_eleventh_is_rate_limited(api, monkeypatch) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant)
    headers = api.auth(user, tenant)

    async with _rate_limited_client(api, monkeypatch, _Clock()) as client:
        allowed = []
        for index in range(_LIMIT):
            upload = _upload_attachment if index % 2 == 0 else _upload_document
            allowed.append(await upload(client, headers))
        limited = await _upload_document(client, headers)

    assert [resp.status_code for resp in allowed] == [201] * _LIMIT
    _assert_rate_limited(limited)


@pytest.mark.asyncio
async def test_the_document_upload_shares_the_attachment_budget(api, monkeypatch) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant)
    headers = api.auth(user, tenant)

    async with _rate_limited_client(api, monkeypatch, _Clock()) as client:
        attachments = [await _upload_attachment(client, headers) for _ in range(_LIMIT)]
        limited = await _upload_document(client, headers)

    assert [resp.status_code for resp in attachments] == [201] * _LIMIT
    _assert_rate_limited(limited)


@pytest.mark.asyncio
async def test_another_users_upload_budget_is_untouched(api, monkeypatch) -> None:
    tenant, spender = await api.seed(role="knowledge_admin")
    other = await api.seed_user(tenant, role="knowledge_admin")
    await api.seed_kb(tenant)

    async with _rate_limited_client(api, monkeypatch, _Clock()) as client:
        spent = [await _upload_attachment(client, api.auth(spender, tenant)) for _ in range(_LIMIT)]
        assert [resp.status_code for resp in spent] == [201] * _LIMIT
        _assert_rate_limited(await _upload_document(client, api.auth(spender, tenant)))

        untouched = await _upload_document(client, api.auth(other, tenant))

    assert untouched.status_code == 201


@pytest.mark.asyncio
async def test_listing_documents_does_not_spend_the_upload_budget(api, monkeypatch) -> None:
    tenant, user = await api.seed(role="knowledge_admin")
    await api.seed_kb(tenant)
    headers = api.auth(user, tenant)

    async with _rate_limited_client(api, monkeypatch, _Clock()) as client:
        listings = [await client.get(_DOCUMENTS_PATH, headers=headers) for _ in range(_LIMIT + 1)]
        # The listing shares the upload path for the POST, but is not an upload:
        # the whole upload budget must still be there afterwards.
        allowed = [await _upload_document(client, headers) for _ in range(_LIMIT)]
        limited = await _upload_document(client, headers)

    assert [resp.status_code for resp in listings] == [200] * (_LIMIT + 1)
    assert [resp.status_code for resp in allowed] == [201] * _LIMIT
    _assert_rate_limited(limited)
