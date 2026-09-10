"""Health endpoint tests."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_live_returns_ok() -> None:
    resp = client.get("/api/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_info() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "eap-backend"
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
