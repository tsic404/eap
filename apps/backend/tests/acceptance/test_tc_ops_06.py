"""TC-ops-06 — the readiness probe's engine entry stays ready with a usable provider.

Scenario semantics: ``docs/03-acceptance.md`` (TC-ops-06 → AC-38, P0) over
``docs/02-contracts.md`` C-17 / C-18. The probe answers 200 with all three
checks normal when the workspace holds at least one usable model provider; an
installed-but-unconfigured provider mixed into the same listing does not flip
the verdict, and the response is the raw shape without the ``data`` envelope.

The listings, console double and dependency stubs come from
``tests/test_health.py``; no part of the readiness judgement is repeated here.
"""

from __future__ import annotations

import pytest

from tests.test_health import (
    _CREDENTIALLED_PROVIDERS,
    _INSTALLED_BUT_UNCONFIGURED_PROVIDERS,
    _ConsoleStub,
    _ready_client,
    _stub_dependency_pings,
)

TC_ID = "TC-ops-06"

# C-17: the raw response is exactly status plus the three dependency checks —
# no `data` envelope.
_READY_BODY = {
    "status": "ok",
    "checks": {"database": "ok", "redis": "ok", "dify": "ok"},
}


def test_usable_provider_keeps_the_probe_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dependency_pings(monkeypatch)
    console = _ConsoleStub(payload=_CREDENTIALLED_PROVIDERS)

    resp = _ready_client(console).get("/api/health/ready")

    assert resp.status_code == 200
    assert resp.json() == _READY_BODY
    assert console.calls == 1


def test_unusable_provider_mixed_in_does_not_flip_the_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dependency_pings(monkeypatch)
    payload = {
        "data": [
            *_CREDENTIALLED_PROVIDERS["data"],
            *_INSTALLED_BUT_UNCONFIGURED_PROVIDERS["data"],
        ]
    }

    resp = _ready_client(_ConsoleStub(payload=payload)).get("/api/health/ready")

    assert resp.status_code == 200
    assert resp.json() == _READY_BODY
