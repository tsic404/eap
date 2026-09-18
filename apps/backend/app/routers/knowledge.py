"""KnowledgeController: FastAPI router for the knowledge-bases resource (§32.3.3)."""

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
    DocumentPageDto,
    DocumentStatusDto,
    KnowledgeBaseDto,
    KnowledgeBasePageDto,
    RetrieveTestDto,
    RetrieveTestResultDto,
)
from app.services.knowledge import KnowledgeService

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])

# "knowledge_admin+" — mutations require knowledge_admin or above in the
# five-tier ladder (platform_admin > agent_admin > knowledge_admin).
_KNOWLEDGE_ADMIN_ROLES = ("knowledge_admin", "agent_admin", "platform_admin")

ActiveTenant = Annotated[Tenant, Depends(get_active_tenant)]
KnowledgeAdmin = Annotated[User, Depends(require_roles(*_KNOWLEDGE_ADMIN_ROLES))]
Session = Annotated[AsyncSession, Depends(get_session)]


def get_knowledge_service(request: Request) -> KnowledgeService:
    """Resolve the app-scoped knowledge service (wired in ``create_app`` lifespan)."""
    return cast(KnowledgeService, request.app.state.knowledge_service)


KnowledgeServiceDep = Annotated[KnowledgeService, Depends(get_knowledge_service)]


@router.get("", response_model=KnowledgeBasePageDto)
async def list_knowledge_bases(
    service: KnowledgeServiceDep,
    tenant: ActiveTenant,
    session: Session,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> KnowledgeBasePageDto:
    offset = (page - 1) * limit
    return await service.list_knowledge_bases(session, tenant, offset=offset, limit=limit)


@router.post("", response_model=KnowledgeBaseDto, status_code=201)
async def create_knowledge_base(
    dto: CreateKnowledgeBaseDto,
    service: KnowledgeServiceDep,
    tenant: ActiveTenant,
    _: KnowledgeAdmin,
    session: Session,
) -> KnowledgeBaseDto:
    return await service.create(session, tenant, dto)


@router.get("/{kb_id}", response_model=KnowledgeBaseDto)
async def get_knowledge_base(
    kb_id: str,
    service: KnowledgeServiceDep,
    tenant: ActiveTenant,
    session: Session,
) -> KnowledgeBaseDto:
    return await service.get(session, tenant, kb_id)


@router.delete("/{kb_id}", status_code=204)
async def delete_knowledge_base(
    kb_id: str,
    service: KnowledgeServiceDep,
    tenant: ActiveTenant,
    _: KnowledgeAdmin,
    session: Session,
) -> None:
    await service.delete(session, tenant, kb_id)


@router.post("/{kb_id}/documents", response_model=DocumentDto, status_code=201)
async def upload_document(
    kb_id: str,
    service: KnowledgeServiceDep,
    tenant: ActiveTenant,
    _: KnowledgeAdmin,
    session: Session,
    file: Annotated[UploadFile, File(...)],
) -> DocumentDto:
    return await service.upload_document(session, tenant, kb_id, file)


@router.get("/{kb_id}/documents", response_model=DocumentPageDto)
async def list_documents(
    kb_id: str,
    service: KnowledgeServiceDep,
    tenant: ActiveTenant,
    session: Session,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DocumentPageDto:
    return await service.list_documents(session, tenant, kb_id, page=page, limit=limit)


@router.get("/{kb_id}/documents/{document_id}/status", response_model=DocumentStatusDto)
async def get_document_status(
    kb_id: str,
    document_id: str,
    service: KnowledgeServiceDep,
    tenant: ActiveTenant,
    session: Session,
) -> DocumentStatusDto:
    return await service.get_document_status(session, tenant, kb_id, document_id)


@router.post("/{kb_id}/retrieval-test", response_model=RetrieveTestResultDto)
async def retrieval_test(
    kb_id: str,
    dto: RetrieveTestDto,
    service: KnowledgeServiceDep,
    tenant: ActiveTenant,
    _: KnowledgeAdmin,
    session: Session,
) -> RetrieveTestResultDto:
    return await service.retrieval_test(session, tenant, kb_id, dto)
