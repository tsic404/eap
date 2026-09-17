"""ToolService: tool CRUD + execute (approval gate) + debug orchestration."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.tool import ToolDebugCase, ToolRegistry
from app.models.user import User
from app.queue import enqueue_tool_approval
from app.repositories.tool_repository import ToolRepository
from app.schemas.tool import CreateToolDto, DebugToolDto, UpdateToolDto
from app.services.tool_proxy import ToolProxy, mask_pii

log = structlog.get_logger(__name__)

_PLATFORM_ADMIN_ROLE = "platform_admin"

# UC-26-4: a pending approval task auto-cancels after 24h (§14.1 state machine).
APPROVAL_TIMEOUT = timedelta(hours=24)


class ToolService:
    """Business orchestration over the tool registry and HTTP proxy."""

    def __init__(
        self,
        session: AsyncSession,
        tool_proxy: ToolProxy,
        repository: ToolRepository | None = None,
    ) -> None:
        self.session = session
        self.tool_proxy = tool_proxy
        self.repository = repository or ToolRepository(session)

    # -- CRUD ---------------------------------------------------------------

    async def create(self, dto: CreateToolDto, tenant: Tenant, user: User) -> ToolRegistry:
        existing = await self.repository.get(dto.tool_id)
        if existing is not None:
            raise AppError(409, "DUPLICATE_TOOL_ID", "Tool ID already exists")
        tool = ToolRegistry(
            tool_id=dto.tool_id,
            tenant_id=tenant.id,
            name=dto.name,
            description=dto.description,
            type=dto.type.value,
            endpoint=dto.endpoint,
            method=dto.method.value,
            risk_level=dto.risk_level.value,
            permission_mode=dto.permission_mode.value,
            auth_type=dto.auth_type.value,
            auth_config=dto.auth_config,
            timeout_ms=dto.timeout_ms,
            retry_policy=dto.retry_policy.model_dump(),
            circuit_breaker=dto.circuit_breaker,
            status="active",
            created_by=user.id,
        )
        created = await self.repository.create(tool)
        await self.session.commit()
        return created

    async def list(self, tenant: Tenant) -> list[ToolRegistry]:
        return await self.repository.list_for_tenant(tenant.id)

    async def get(self, tool_id: str, tenant: Tenant) -> ToolRegistry:
        tool = await self.repository.get(tool_id)
        if tool is None or (tool.tenant_id is not None and tool.tenant_id != tenant.id):
            raise AppError(404, "TOOL_NOT_FOUND", "Tool not found")
        return tool

    async def update(
        self, tool_id: str, dto: UpdateToolDto, tenant: Tenant, user: User
    ) -> ToolRegistry:
        tool = await self.get(tool_id, tenant)
        self._ensure_writable(tool, user)
        for field, value in dto.model_dump(exclude_unset=True, mode="json").items():
            setattr(tool, field, value)
        await self.session.commit()
        # Re-sync server-computed columns: ``updated_at`` has an
        # ``onupdate=func.now()`` server expression, so after commit its value
        # is expired; refreshing here keeps ``ToolRead.model_validate`` from
        # lazy-loading it outside a greenlet (MissingGreenlet).
        await self.session.refresh(tool)
        return tool

    async def delete(self, tool_id: str, tenant: Tenant, user: User) -> None:
        tool = await self.get(tool_id, tenant)
        self._ensure_writable(tool, user)
        await self.repository.delete(tool)
        await self.session.commit()

    @staticmethod
    def _ensure_writable(tool: ToolRegistry, user: User) -> None:
        # Global tools (tenant_id IS NULL) are shared across tenants: only a
        # platform admin may mutate them, otherwise any tenant admin could
        # overwrite or delete a platform-wide tool (cross-tenant write).
        if tool.tenant_id is None and user.role != _PLATFORM_ADMIN_ROLE:
            raise AppError(403, "FORBIDDEN", "Global tools require platform admin")

    # -- Execute / debug -----------------------------------------------------

    async def execute(
        self,
        tool: ToolRegistry,
        params: dict[str, Any],
        *,
        requester: User,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a tool on behalf of an agent run, gating on risk/approval."""
        if tool.permission_mode == "disabled":
            raise AppError(409, "TOOL_DISABLED", "Tool is disabled")
        if tool.risk_level == "high" or tool.permission_mode == "confirm":
            task = await self._request_approval(tool, params, conversation_id, requester)
            return {"status": "pending_approval", "task_id": str(task.id)}
        result = await self.tool_proxy.execute(tool, params)
        return {
            "status": "success",
            "status_code": result.status_code,
            "body": result.body,
            "latency_ms": result.latency_ms,
        }

    async def debug(self, tool: ToolRegistry, dto: DebugToolDto) -> dict[str, Any]:
        """Run a test call for the admin debug panel and optionally save a case."""
        if tool.permission_mode == "disabled":
            raise AppError(409, "TOOL_DISABLED", "Tool is disabled")
        result = await self.tool_proxy.debug(tool, dto.params)
        saved_case_id = None
        if dto.save_as_test_case:
            case = ToolDebugCase(
                tool_id=tool.tool_id,
                name=dto.name or f"{tool.name} debug",
                request_body=json.dumps(mask_pii(dto.params), ensure_ascii=False),
                response_body=json.dumps(result.body, ensure_ascii=False, default=str),
                status_code=result.status_code,
                latency_ms=result.latency_ms,
            )
            self.session.add(case)
            await self.session.commit()
            await self.session.refresh(case)
            saved_case_id = case.id
        return {
            "statusCode": result.status_code,
            "responseBody": result.body,
            "latencyMs": result.latency_ms,
            "savedCaseId": str(saved_case_id) if saved_case_id else None,
        }

    # -- Approval ------------------------------------------------------------

    async def _request_approval(
        self,
        tool: ToolRegistry,
        params: dict[str, Any],
        conversation_id: str | None,
        requester: User,
    ) -> Task:
        task = Task(
            tenant_id=requester.tenant_id,
            creator_id=requester.id,
            type="tool_approval",
            title=f"工具审批：{tool.name}",
            priority="high",
            status="pending",
            expires_at=datetime.now(UTC) + APPROVAL_TIMEOUT,
            payload={
                "tool_id": tool.tool_id,
                "tool_name": tool.name,
                # The task row is the approver's decision payload (P1 polling):
                # keep real params so they can adjudicate on actual values.
                # Masking is reserved for non-decision copies (debug cases,
                # audit trail).
                "params": params,
                "conversation_id": conversation_id,
            },
        )
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        try:
            # Offload the synchronous Redis enqueue; approval dispatch is
            # best-effort and must never fail the business request.
            await asyncio.to_thread(
                enqueue_tool_approval,
                {
                    "task_id": str(task.id),
                    "tool_id": tool.tool_id,
                    "tool_name": tool.name,
                    "tenant_id": str(requester.tenant_id),
                    "requester_id": str(requester.id),
                    "conversation_id": conversation_id,
                },
            )
        except Exception:
            log.warning("tool_approval_enqueue_failed", task_id=str(task.id), exc_info=True)
        return task
