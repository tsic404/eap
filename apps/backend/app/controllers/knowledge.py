"""Knowledge-base HTTP router (arch doc §32.3.3)."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import get_active_tenant, require_roles
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.knowledge import (
    CreateKnowledgeBaseDto,
    DocumentDto,
    DocumentStatusDto,
    KnowledgeBaseDto,
    RetrieveTestDto,
    RetrieveTestResultDto,
)
from app.services.knowledge import KnowledgeService

# "knowledge_admin+" — the five-tier ladder is platform_admin > agent_admin >
# knowledge_admin > auditor > employee; mutations require knowledge_admin or
# above.
_KNOWLEDGE_ADMIN_ROLES = ("knowledge_admin", "agent_admin", "platform_admin")

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])


def get_knowledge_service(request: Request) -> KnowledgeService:
    """Resolve the app-scoped knowledge service (wired in ``create_app`` lifespan)."""
    return cast(KnowledgeService, request.app.state.knowledge_service)


_KnowledgeService = Annotated[KnowledgeService, Depends(get_knowledge_service)]
_Session = Annotated[AsyncSession, Depends(get_session)]
_Tenant = Annotated[Tenant, Depends(get_active_tenant)]
_KnowledgeAdmin = Annotated[User, Depends(require_roles(*_KNOWLEDGE_ADMIN_ROLES))]


@router.get("", response_model=list[KnowledgeBaseDto])
async def list_knowledge_bases(
    service: _KnowledgeService,
    session: _Session,
    tenant: _Tenant,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[KnowledgeBaseDto]:
    offset = (page - 1) * limit
    return await service.list_knowledge_bases(session, tenant, offset=offset, limit=limit)


@router.post("", response_model=KnowledgeBaseDto, status_code=201)
async def create_knowledge_base(
    body: CreateKnowledgeBaseDto,
    service: _KnowledgeService,
    session: _Session,
    tenant: _Tenant,
    _: _KnowledgeAdmin,
) -> KnowledgeBaseDto:
    return await service.create(session, tenant, body)


@router.get("/{kb_id}", response_model=KnowledgeBaseDto)
async def get_knowledge_base(
    kb_id: str,
    service: _KnowledgeService,
    session: _Session,
    tenant: _Tenant,
) -> KnowledgeBaseDto:
    return await service.get(session, tenant, kb_id)


@router.delete("/{kb_id}", status_code=204)
async def delete_knowledge_base(
    kb_id: str,
    service: _KnowledgeService,
    session: _Session,
    tenant: _Tenant,
    _: _KnowledgeAdmin,
) -> None:
    await service.delete(session, tenant, kb_id)


@router.post("/{kb_id}/documents", response_model=DocumentDto, status_code=201)
async def upload_document(
    kb_id: str,
    service: _KnowledgeService,
    session: _Session,
    tenant: _Tenant,
    _: _KnowledgeAdmin,
    file: Annotated[UploadFile, File(...)],
) -> DocumentDto:
    return await service.upload_document(session, tenant, kb_id, file)


@router.get("/{kb_id}/documents", response_model=list[DocumentDto])
async def list_documents(
    kb_id: str,
    service: _KnowledgeService,
    session: _Session,
    tenant: _Tenant,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[DocumentDto]:
    return await service.list_documents(session, tenant, kb_id, page=page, limit=limit)


@router.get("/{kb_id}/documents/{document_id}/status", response_model=DocumentStatusDto)
async def get_document_status(
    kb_id: str,
    document_id: str,
    service: _KnowledgeService,
    session: _Session,
    tenant: _Tenant,
) -> DocumentStatusDto:
    return await service.get_document_status(session, tenant, kb_id, document_id)


@router.post("/{kb_id}/retrieval-test", response_model=RetrieveTestResultDto)
async def retrieval_test(
    kb_id: str,
    body: RetrieveTestDto,
    service: _KnowledgeService,
    session: _Session,
    tenant: _Tenant,
    _: _KnowledgeAdmin,
) -> RetrieveTestResultDto:
    return await service.retrieval_test(session, tenant, kb_id, body)
