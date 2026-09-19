"""Files router HTTP tests: auth guard, multipart upload, upload-file id contract."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_session
from app.dify_console import DifyConsoleError
from app.main import create_app
from app.models.tenant import Tenant
from app.models.user import User


def _make_tenant() -> Tenant:
    return Tenant(
        id=uuid.uuid4(),
        name="Tenant",
        slug=f"tenant-{uuid.uuid4().hex[:8]}",
        sso_provider="local",
        status="active",
        quota_limit=10000,
        quota_used=0,
    )


def _make_user(tenant: Tenant) -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        sso_sub="test-sub",
        email="user@example.com",
        name="Test User",
        role="employee",
        status="active",
    )


def _keypair() -> tuple[object, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_key, public_pem


def _signed_token(private_key: object, user: User, tenant: Tenant) -> str:
    payload: dict[str, str] = {"sub": str(user.id), "role": user.role, "tenantId": str(tenant.id)}
    return pyjwt.encode(payload, private_key, algorithm="RS256")


def _client(dify: AsyncMock, tenant: Tenant) -> tuple[TestClient, str]:
    private_key, public_pem = _keypair()
    user = _make_user(tenant)
    app: FastAPI = create_app(
        settings=Settings(_env_file=None, jwt_public_key=public_pem, rate_limit_enabled=False)
    )
    app.state.dify_console = dify

    session = AsyncMock()

    async def _get(model: object, pk: object) -> object | None:
        if model is User:
            return user
        if model is Tenant:
            return tenant
        return None

    session.get = AsyncMock(side_effect=_get)

    async def _override_session() -> AsyncMock:
        return session

    app.dependency_overrides[get_session] = _override_session
    token = _signed_token(private_key, user, tenant)
    return TestClient(app, raise_server_exceptions=False), token


def test_upload_file_returns_upload_file_id() -> None:
    """The response ``id`` is Dify's upload-file id, not a document id."""
    tenant = _make_tenant()
    dify = AsyncMock()
    dify.upload_file.return_value = {"id": "file-1"}
    client, token = _client(dify, tenant)

    resp = client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("report.pdf", b"%PDF-1.7 content", "application/pdf")},
    )

    assert resp.status_code == 201
    assert resp.json()["data"] == {"id": "file-1", "name": "report.pdf", "type": "document"}
    dify.upload_file.assert_awaited_once()


def test_upload_exe_returns_422_unsupported_format() -> None:
    tenant = _make_tenant()
    dify = AsyncMock()
    client, token = _client(dify, tenant)

    resp = client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNSUPPORTED_FORMAT"
    dify.upload_file.assert_not_awaited()


def test_upload_requires_authentication() -> None:
    tenant = _make_tenant()
    dify = AsyncMock()
    client, _ = _client(dify, tenant)

    resp = client.post(
        "/api/files/upload",
        files={"file": ("report.pdf", b"%PDF-1.7 content", "application/pdf")},
    )

    assert resp.status_code == 401
    dify.upload_file.assert_not_awaited()


def test_upload_dify_error_returns_502() -> None:
    """A Dify Console failure maps to 502 ``DIFY_UPLOAD_FAILED``, not a generic 500."""
    tenant = _make_tenant()
    dify = AsyncMock()
    dify.upload_file.side_effect = DifyConsoleError(500, "upstream down")
    client, token = _client(dify, tenant)

    resp = client.post(
        "/api/files/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("report.pdf", b"%PDF-1.7 content", "application/pdf")},
    )

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "DIFY_UPLOAD_FAILED"
