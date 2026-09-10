"""Global exception handler tests — the ``{"error": …}`` envelope."""

from fastapi.testclient import TestClient


def test_unhandled_exception_returns_internal_error(client: TestClient) -> None:
    resp = client.get("/api/test/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "Internal server error"


def test_http_exception_returns_structured_error(client: TestClient) -> None:
    resp = client.get("/api/test/missing")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "NOT_FOUND", "message": "resource not found"}}


def test_validation_error_returns_422(client: TestClient) -> None:
    resp = client.post("/api/test/validate", json={})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body["error"].get("details"), list)
