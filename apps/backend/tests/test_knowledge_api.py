"""Knowledge router HTTP tests: role guard, multipart upload, response envelope."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models.knowledge import KnowledgeBaseRegistry
from app.models.tenant import Tenant
from app.models.user import User
from app.services.knowledge import KnowledgeService


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


def _make_user(tenant: Tenant, role: str) -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        sso_sub="test-sub",
        email="user@example.com",
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
    payload: dict[str, str] = {"sub": str(user.id), "role": user.role, "tenantId": str(tenant.id)}
    return pyjwt.encode(payload, private_key, algorithm="RS256")


def _kb(tenant: Tenant) -> KnowledgeBaseRegistry:
    return KnowledgeBaseRegistry(
        kb_id="kb-1",
        tenant_id=tenant.id,
        dify_dataset_id="ds-1",
        dify_api_key="ds-secret",
        name="Knowledge",
        description=None,
        type="business",
        indexing_status="ready",
        doc_count=0,
        chunk_count=0,
        created_at=datetime.now(UTC),
    )


class _Repo:
    def __init__(self, kb: KnowledgeBaseRegistry | None = None) -> None:
        self.kb = kb

    async def get_for_tenant(
        self, session: Any, kb_id: str, tenant_id: uuid.UUID
    ) -> KnowledgeBaseRegistry | None:
        if self.kb is not None and self.kb.kb_id == kb_id and self.kb.tenant_id == tenant_id:
            return self.kb
        return None

    async def list_for_tenant(
        self, session: Any, tenant_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[list[KnowledgeBaseRegistry], int]:
        rows = [self.kb] if self.kb is not None else []
        return rows, len(rows)

    async def create(
        self,
        session: Any,
        *,
        tenant_id: uuid.UUID,
        dify_dataset_id: str,
        dify_api_key: str | None,
        name: str,
        description: str | None,
        type: str,
    ) -> KnowledgeBaseRegistry:
        return KnowledgeBaseRegistry(
            kb_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            dify_dataset_id=dify_dataset_id,
            dify_api_key=dify_api_key,
            name=name,
            description=description,
            type=type,
            indexing_status="ready",
            doc_count=0,
            chunk_count=0,
            created_at=datetime.now(UTC),
        )

    async def delete(self, session: Any, kb: KnowledgeBaseRegistry) -> None:
        return None

    async def insert_document(
        self, session: Any, *, kb_id: str, document_id: str, name: str
    ) -> None:
        return None

    async def transition_document_status(
        self,
        session: Any,
        *,
        kb_id: str,
        document_id: str,
        status: str,
        error: str | None = None,
    ) -> bool:
        return True


def _authed_client(
    service: KnowledgeService, *, role: str, tenant: Tenant
) -> tuple[TestClient, str]:
    private_key, public_pem = _keypair()
    user = _make_user(tenant, role)
    app: FastAPI = create_app(
        settings=Settings(_env_file=None, jwt_public_key=public_pem, rate_limit_enabled=False)
    )
    app.state.knowledge_service = service

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


def _service(dify: AsyncMock, *, repo: _Repo) -> KnowledgeService:
    return KnowledgeService(dify, settings=Settings(_env_file=None), repository=repo)


def test_create_knowledge_base_requires_knowledge_admin() -> None:
    tenant = _make_tenant()
    dify = AsyncMock()
    service = _service(dify, repo=_Repo())
    client, token = _authed_client(service, role="employee", tenant=tenant)

    resp = client.post(
        "/api/knowledge-bases",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "KB"},
    )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"
    dify.create_dataset.assert_not_awaited()


def test_create_knowledge_base_returns_201() -> None:
    tenant = _make_tenant()
    dify = AsyncMock()
    dify.create_dataset.return_value = {"id": "ds-1"}
    dify.get_dataset_api_keys.return_value = {"data": [{"token": "ds-secret"}]}
    service = _service(dify, repo=_Repo())
    client, token = _authed_client(service, role="knowledge_admin", tenant=tenant)

    resp = client.post(
        "/api/knowledge-bases",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "KB", "description": "docs"},
    )

    assert resp.status_code == 201
    body = resp.json()["data"]
    assert body["name"] == "KB"
    assert body["kb_id"]
    assert body["indexing_status"] == "ready"


def test_list_knowledge_bases_returns_items_and_total() -> None:
    tenant = _make_tenant()
    service = _service(AsyncMock(), repo=_Repo(_kb(tenant)))
    client, token = _authed_client(service, role="employee", tenant=tenant)

    resp = client.get(
        "/api/knowledge-bases", headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["kb_id"] == "kb-1"


def test_list_documents_returns_items_and_total() -> None:
    tenant = _make_tenant()
    dify = AsyncMock()
    dify.list_documents.return_value = {
        "data": [{"id": "doc-1", "name": "a.pdf", "indexing_status": "indexing"}],
        "total": 3,
    }
    service = _service(dify, repo=_Repo(_kb(tenant)))
    client, token = _authed_client(service, role="employee", tenant=tenant)

    resp = client.get(
        "/api/knowledge-bases/kb-1/documents",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["total"] == 3
    assert body["items"][0]["id"] == "doc-1"


def test_upload_pdf_returns_201_indexing() -> None:
    tenant = _make_tenant()
    dify = AsyncMock()
    dify.upload_file.return_value = {"id": "file-1"}
    dify.create_document.return_value = {
        "documents": [{"id": "doc-1", "indexing_status": "indexing"}]
    }
    service = _service(dify, repo=_Repo(_kb(tenant)))
    client, token = _authed_client(service, role="knowledge_admin", tenant=tenant)

    resp = client.post(
        "/api/knowledge-bases/kb-1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("report.pdf", b"%PDF-1.7 content", "application/pdf")},
    )

    assert resp.status_code == 201
    assert resp.json()["data"] == {"id": "doc-1", "name": "report.pdf", "status": "indexing"}


def test_upload_exe_returns_422_unsupported_format() -> None:
    tenant = _make_tenant()
    dify = AsyncMock()
    service = _service(dify, repo=_Repo(_kb(tenant)))
    client, token = _authed_client(service, role="knowledge_admin", tenant=tenant)

    resp = client.post(
        "/api/knowledge-bases/kb-1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "UNSUPPORTED_FORMAT"
    dify.upload_file.assert_not_awaited()


def test_get_document_status_returns_polled_status() -> None:
    tenant = _make_tenant()
    dify = AsyncMock()
    dify.get_document_indexing_status.return_value = {"indexing_status": "indexing"}
    service = _service(dify, repo=_Repo(_kb(tenant)))
    client, token = _authed_client(service, role="knowledge_admin", tenant=tenant)

    resp = client.get(
        "/api/knowledge-bases/kb-1/documents/doc-1/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {"id": "doc-1", "status": "indexing", "error": None}
