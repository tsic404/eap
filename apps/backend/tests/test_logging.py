"""structlog observability contract tests (§22.3)."""

from __future__ import annotations

import io
import json
import logging

import structlog

from app.logging_conf import _canonicalise_observability_fields, configure_logging


def test_canonicalise_processor_emits_required_fields() -> None:
    event = {
        "event": "request_completed",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "duration_ms": 42.5,
        "path": "/api/x",
    }
    out = _canonicalise_observability_fields(None, "", dict(event))
    for field in ("action", "resource", "resourceId", "tenantId", "userId", "duration"):
        assert field in out
    assert out["action"] == "request_completed"
    assert out["tenantId"] == "tenant-1"
    assert out["userId"] == "user-1"
    assert out["duration"] == 42.5
    assert out["resource"] is None
    assert out["resourceId"] is None
    # snake_case context keys are promoted, not duplicated.
    assert "tenant_id" not in out
    assert "user_id" not in out
    assert "duration_ms" not in out


def test_canonicalise_processor_does_not_override_explicit_camel_case() -> None:
    event = {"event": "x", "tenantId": "explicit", "tenant_id": "context"}
    out = _canonicalise_observability_fields(None, "", dict(event))
    assert out["tenantId"] == "explicit"


def test_log_json_output_contains_required_fields() -> None:
    configure_logging(level="INFO", json_output=True)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        structlog.get_logger("test").info(
            "do_thing",
            resource="http",
            resourceId="/api/x",
            tenant_id="tenant-1",
            user_id="user-1",
            duration_ms=7.0,
        )
    finally:
        root.removeHandler(handler)

    line = stream.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    for field in ("action", "resource", "resourceId", "tenantId", "userId", "duration"):
        assert field in payload
    assert payload["action"] == "do_thing"
    assert payload["resource"] == "http"
    assert payload["resourceId"] == "/api/x"
    assert payload["tenantId"] == "tenant-1"
    assert payload["userId"] == "user-1"
    assert payload["duration"] == 7.0
