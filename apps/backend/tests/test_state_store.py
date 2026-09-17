"""OidcStateStore contract tests: SET NX EX + GETDEL single-use semantics."""

from __future__ import annotations

import pytest

from app.auth.state_store import OidcStateStore
from tests.conftest import FakeRedis


@pytest.fixture
def store() -> OidcStateStore:
    return OidcStateStore(FakeRedis())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_put_then_consume_returns_payload_once(store: OidcStateStore) -> None:
    assert await store.put("state-1", {"code_verifier": "v", "nonce": "n"}) is True

    payload = await store.consume("state-1")
    assert payload == {"code_verifier": "v", "nonce": "n"}

    # GETDEL consumes: a second read yields nothing.
    assert await store.consume("state-1") is None


@pytest.mark.asyncio
async def test_put_rejects_duplicate_state(store: OidcStateStore) -> None:
    assert await store.put("state-2", {"nonce": "first"}) is True
    # SET NX fails on an existing key, so a colliding state is refused.
    assert await store.put("state-2", {"nonce": "second"}) is False


@pytest.mark.asyncio
async def test_consume_unknown_state_returns_none(store: OidcStateStore) -> None:
    assert await store.consume("missing") is None
