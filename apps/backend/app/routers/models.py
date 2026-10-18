"""ModelController: list Dify model providers (architecture §15.2).

Providers are Dify workspace-scoped resources, so the listing itself carries no
tenant scoping and the response is the same for every tenant. The request is
still a business one, so the tenant verdict (existence, status, quota) is
reached ahead of the endpoint's role set, as on every other ``/api`` route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.dependencies import require_tenant_roles
from app.models.user import User
from app.schemas.model import ModelProviderDto
from app.services.model import ModelService

router = APIRouter(prefix="/api/models", tags=["models"])

_MODEL_READ_ROLES = ("platform_admin", "agent_admin")


@router.get("", response_model=list[ModelProviderDto])
async def list_models(
    request: Request,
    _: User = Depends(require_tenant_roles(*_MODEL_READ_ROLES)),
) -> list[ModelProviderDto]:
    service = ModelService(request.app.state.dify_console)
    return await service.list_providers()
