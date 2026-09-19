"""ModelController HTTP tests: role gating, Dify proxy, and envelope."""

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


def _make_user(tenant: Tenant, role: str = "employee") -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        sso_sub=f"sub-{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        name="Test User",
        role=role,
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
    payload = {"sub": str(user.id), "role": user.role, "tenantId": str(tenant.id)}
    return pyjwt.encode(payload, private_key, algorithm="RS256")


def _make_session(user: User) -> AsyncMock:
    session = AsyncMock()

    async def _get(model: object, pk: object, **kwargs: object) -> object | None:
        if model is User:
            return user
        return None

    session.get = AsyncMock(side_effect=_get)
    return session


def _make_client(
    user: User, tenant: Tenant, session: AsyncMock, monkeypatch
) -> tuple[TestClient, str]:
    private_key, public_pem = _keypair()
    token = _signed_token(private_key, user, tenant)

    async def _override_session() -> AsyncMock:
        return session

    app: FastAPI = create_app(
        Settings(_env_file=None, jwt_public_key=public_pem, rate_limit_enabled=False)
    )
    app.dependency_overrides[get_session] = _override_session
    return TestClient(app, raise_server_exceptions=False), token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_list_models_requires_admin_role(monkeypatch) -> None:
    tenant = _make_tenant()
    user = _make_user(tenant, role="employee")
    client, token = _make_client(user, tenant, _make_session(user), monkeypatch)

    resp = client.get("/api/models", headers=_auth(token))

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_list_models_returns_providers_for_admin(monkeypatch) -> None:
    tenant = _make_tenant()
    user = _make_user(tenant, role="platform_admin")

    fake_console = AsyncMock()
    fake_console.get_model_providers = AsyncMock(
        return_value={
            "data": [
                {
                    "provider": "openai",
                    "label": {"zh_Hans": "OpenAI"},
                    "preferred_provider_type": "custom",
                    "custom_configuration": {
                        "models": [{"model": "gpt-4o"}, {"model": "gpt-4o-mini"}]
                    },
                }
            ]
        }
    )

    private_key, public_pem = _keypair()
    token = _signed_token(private_key, user, tenant)

    async def _override_session() -> AsyncMock:
        return _make_session(user)

    app: FastAPI = create_app(
        Settings(_env_file=None, jwt_public_key=public_pem, rate_limit_enabled=False)
    )
    app.state.dify_console = fake_console
    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/models", headers=_auth(token))

    assert resp.status_code == 200
    providers = resp.json()["data"]
    assert providers == [
        {
            "provider": "openai",
            "label": "OpenAI",
            "deploymentType": "custom",
            "modelCount": 2,
        }
    ]
