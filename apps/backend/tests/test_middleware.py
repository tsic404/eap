"""Middleware pipeline tests: transform envelope, CORS, helmet, request id, rate limit."""

import base64
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.middleware.security import RateLimitMiddleware


def test_success_response_wrapped_in_data(client: TestClient) -> None:
    resp = client.get("/api/test/echo")
    assert resp.status_code == 200
    assert resp.json() == {"data": {"hello": "world"}}


def test_cors_allows_whitelisted_origin(client: TestClient) -> None:
    resp = client.get("/api/health/live", headers={"Origin": "http://localhost:3000"})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_non_whitelisted_origin(client: TestClient) -> None:
    resp = client.get("/api/health/live", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403
    assert resp.json() == {
        "error": {"code": "FORBIDDEN", "message": "Origin not allowed by CORS policy"}
    }


def test_cors_preflight_whitelisted_origin(client: TestClient) -> None:
    resp = client.options(
        "/api/test/echo",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert resp.headers["access-control-max-age"] == "600"


def test_helmet_headers_present(client: TestClient) -> None:
    resp = client.get("/api/health/live")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"


def test_request_id_generated_and_echoed(client: TestClient) -> None:
    resp = client.get("/api/health/live")
    assert resp.headers["x-request-id"]

    resp = client.get("/api/health/live", headers={"X-Request-Id": "trace-me"})
    assert resp.headers["x-request-id"] == "trace-me"


def test_rate_limit_rejects_excess_requests() -> None:
    settings = Settings(_env_file=None, rate_limit_requests=1, rate_limit_window_seconds=60)
    app: FastAPI = create_app(settings=settings)

    @app.get("/api/test/echo")
    def echo() -> dict[str, str]:
        return {"hello": "world"}

    with TestClient(app) as client:
        assert client.get("/api/test/echo").status_code == 200
        resp = client.get("/api/test/echo")
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "RATE_LIMITED"


def test_rate_limiter_ignores_spoofed_x_forwarded_for() -> None:
    scope = {"headers": [(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8")], "client": ("9.9.9.9", 1234)}

    # No trusted proxies configured: X-Forwarded-For is ignored, peer IP wins.
    default = RateLimitMiddleware(None, Settings(_env_file=None))
    assert default._client_ip(scope) == "9.9.9.9"

    # One trusted proxy: the rightmost (proxy-appended) hop is the real client,
    # not the leftmost client-spoofable value.
    behind_proxy = RateLimitMiddleware(None, Settings(_env_file=None, trusted_proxy_count=1))
    assert behind_proxy._client_ip(scope) == "5.6.7.8"


def test_rate_limiter_handles_degenerate_x_forwarded_for() -> None:
    # A header of only separators/whitespace yields no usable hop and must fall
    # back to the socket peer instead of raising IndexError.
    scope = {"headers": [(b"x-forwarded-for", b", ,")], "client": ("9.9.9.9", 1234)}
    middleware = RateLimitMiddleware(None, Settings(_env_file=None, trusted_proxy_count=1))
    assert middleware._client_ip(scope) == "9.9.9.9"


def test_streaming_response_not_wrapped_or_truncated() -> None:
    app: FastAPI = create_app()

    @app.get("/api/test/stream")
    def stream() -> StreamingResponse:
        async def generate():
            yield "chunk-one"
            yield "chunk-two"

        return StreamingResponse(generate(), media_type="text/plain")

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/test/stream")
        assert resp.status_code == 200
        # Both chunks arrive intact and the body is not wrapped in the envelope.
        assert resp.text == "chunk-onechunk-two"



def _ident_handler(request: Request) -> dict[str, object]:
    return {
        "user_id": getattr(request.state, "user_id", None),
        "tenant_id": getattr(request.state, "tenant_id", None),
        "role": getattr(request.state, "role", None),
    }


def test_jwt_without_public_key_is_not_trusted() -> None:
    token = _unsigned_jwt({"sub": "user-123", "tenantId": "tenant-456", "role": "admin"})
    app: FastAPI = create_app()
    app.get("/api/test/ident")(_ident_handler)

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/test/ident", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json() == {"data": {"user_id": None, "tenant_id": None, "role": ""}}


def test_jwt_with_public_key_verifies_signature() -> None:
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    token = pyjwt.encode(
        {"sub": "user-123", "tenantId": "tenant-456", "role": "admin"},
        private_key,
        algorithm="RS256",
    )

    app = create_app(settings=Settings(_env_file=None, jwt_public_key=public_pem))
    app.get("/api/test/ident")(_ident_handler)

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/test/ident", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json() == {
            "data": {"user_id": "user-123", "tenant_id": "tenant-456", "role": "admin"}
        }


def _unsigned_jwt(payload: dict[str, str]) -> str:
    def encode(data: dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{encode({'alg': 'none', 'typ': 'JWT'})}.{encode(payload)}."
