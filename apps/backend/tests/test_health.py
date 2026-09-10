"""Health endpoint tests."""

from fastapi.testclient import TestClient


def test_health_live_returns_raw_ok(client: TestClient) -> None:
    resp = client.get("/api/health/live")
    assert resp.status_code == 200
    # Health probes are the raw-response exception — no data envelope.
    assert resp.json() == {"status": "ok"}


def test_health_info_returns_raw_service_info(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "eap-backend"
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"


def test_health_ready_reports_dependency_status(client: TestClient) -> None:
    resp = client.get("/api/health/ready")
    body = resp.json()
    # Without Postgres/Redis/Dify the probe reports degraded but still returns
    # the structured per-dependency status.
    assert set(body["checks"]) == {"database", "redis", "dify"}
    assert body["status"] in {"ok", "degraded"}
    for value in body["checks"].values():
        assert value in {"ok", "error"}
    assert resp.status_code == (200 if body["status"] == "ok" else 503)
