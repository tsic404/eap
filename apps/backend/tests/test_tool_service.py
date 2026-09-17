"""ToolService unit tests (mocked session + MockTransport proxy)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.errors import AppError
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.tool import ToolDebugCase, ToolRegistry
from app.models.user import User
from app.schemas.tool import CreateToolDto, DebugToolDto, UpdateToolDto
from app.services.tool_proxy import ToolProxy
from app.services.tool_service import ToolService


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


def _make_user(tenant: Tenant | None = None, role: str = "employee") -> User:
    return User(
        id=uuid.uuid4(),
        tenant_id=(tenant or _make_tenant()).id,
        sso_sub="test-sub",
        email="user@example.com",
        name="Test User",
        role=role,
        status="active",
    )


def _make_tool(**overrides: object) -> ToolRegistry:
    fields: dict[str, object] = {
        "tool_id": "my-tool",
        "tenant_id": None,
        "name": "My Tool",
        "type": "http",
        "endpoint": "http://tool.example/api",
        "method": "POST",
        "risk_level": "medium",
        "permission_mode": "auto",
        "auth_type": "none",
        "auth_config": None,
        "timeout_ms": 10000,
        "retry_policy": {"max_retries": 2, "base_delay_ms": 1000},
        "status": "active",
    }
    fields.update(overrides)
    return ToolRegistry(**fields)  # type: ignore[arg-type]


def _make_session(**overrides: object) -> AsyncMock:
    session = AsyncMock()
    # ``AsyncSession.add`` is synchronous; ``delete``/``flush``/``commit``/
    # ``refresh``/``get`` are awaited.
    session.add = Mock()
    session.delete = AsyncMock()

    async def _refresh(obj: object) -> None:
        if isinstance(obj, (Task, ToolDebugCase)) and obj.id is None:
            obj.id = uuid.uuid4()

    session.refresh = AsyncMock(side_effect=_refresh)
    session.get = AsyncMock(return_value=None)
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


def _service(session: AsyncMock | None = None, *, ok: bool = True) -> tuple[ToolService, AsyncMock]:
    session = session or _make_session()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if ok else 500, json={"ok": ok})

    proxy = ToolProxy(transport=httpx.MockTransport(handler))
    return ToolService(session, proxy), session


# -- CRUD --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_active_tool_and_commits() -> None:
    service, session = _service()
    tenant = _make_tenant()
    user = _make_user(tenant)
    dto = CreateToolDto(name="T", tool_id="t1", endpoint="http://x", risk_level="low")

    tool = await service.create(dto, tenant, user)

    assert tool.status == "active"
    assert tool.tool_id == "t1"
    assert tool.tenant_id == tenant.id
    assert tool.created_by == user.id
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_duplicate_tool_id_raises_409() -> None:
    session = _make_session(get=AsyncMock(return_value=_make_tool(tool_id="t1")))
    service, _ = _service(session)
    tenant = _make_tenant()
    user = _make_user(tenant)
    dto = CreateToolDto(name="T", tool_id="t1", endpoint="http://x", risk_level="low")

    with pytest.raises(AppError) as exc:
        await service.create(dto, tenant, user)
    assert exc.value.status_code == 409
    assert exc.value.code == "DUPLICATE_TOOL_ID"


@pytest.mark.asyncio
async def test_get_returns_tool_for_tenant() -> None:
    tenant = _make_tenant()
    tool = _make_tool(tenant_id=tenant.id)
    session = _make_session(get=AsyncMock(return_value=tool))
    service, _ = _service(session)

    result = await service.get("my-tool", tenant)
    assert result is tool


@pytest.mark.asyncio
async def test_get_cross_tenant_tool_raises_404() -> None:
    other = _make_tenant()
    tool = _make_tool(tenant_id=other.id)
    session = _make_session(get=AsyncMock(return_value=tool))
    service, _ = _service(session)

    with pytest.raises(AppError) as exc:
        await service.get("my-tool", _make_tenant())
    assert exc.value.status_code == 404
    assert exc.value.code == "TOOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_update_applies_fields_and_commits() -> None:
    tenant = _make_tenant()
    tool = _make_tool(tenant_id=tenant.id)
    session = _make_session(get=AsyncMock(return_value=tool))
    service, _ = _service(session)

    updated = await service.update(
        "my-tool",
        UpdateToolDto(name="New", risk_level="high"),
        tenant,
        _make_user(tenant, role="agent_admin"),
    )

    assert updated.name == "New"
    assert updated.risk_level == "high"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_commits() -> None:
    tenant = _make_tenant()
    tool = _make_tool(tenant_id=tenant.id)
    session = _make_session(get=AsyncMock(return_value=tool))
    service, _ = _service(session)

    await service.delete("my-tool", tenant, _make_user(tenant, role="agent_admin"))
    session.delete.assert_awaited_once_with(tool)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_global_tool_by_tenant_admin_raises_403() -> None:
    tool = _make_tool(tenant_id=None)
    session = _make_session(get=AsyncMock(return_value=tool))
    service, _ = _service(session)

    with pytest.raises(AppError) as exc:
        await service.update(
            "my-tool", UpdateToolDto(name="Hacked"), _make_tenant(), _make_user(role="agent_admin")
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "FORBIDDEN"
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_global_tool_by_tenant_admin_raises_403() -> None:
    tool = _make_tool(tenant_id=None)
    session = _make_session(get=AsyncMock(return_value=tool))
    service, _ = _service(session)

    with pytest.raises(AppError) as exc:
        await service.delete("my-tool", _make_tenant(), _make_user(role="agent_admin"))
    assert exc.value.status_code == 403
    session.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_global_tool_by_platform_admin_allowed() -> None:
    tool = _make_tool(tenant_id=None)
    session = _make_session(get=AsyncMock(return_value=tool))
    service, _ = _service(session)

    updated = await service.update(
        "my-tool", UpdateToolDto(name="OK"), _make_tenant(), _make_user(role="platform_admin")
    )
    assert updated.name == "OK"
    session.commit.assert_awaited_once()


# -- execute -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_auto_delegates_to_proxy() -> None:
    service, _ = _service()
    tool = _make_tool(risk_level="medium", permission_mode="auto")

    result = await service.execute(tool, {"a": 1}, requester=_make_user())

    assert result["status"] == "success"
    assert result["status_code"] == 200


@pytest.mark.asyncio
async def test_execute_disabled_raises_409() -> None:
    service, _ = _service()
    tool = _make_tool(permission_mode="disabled")

    with pytest.raises(AppError) as exc:
        await service.execute(tool, {}, requester=_make_user())
    assert exc.value.status_code == 409
    assert exc.value.code == "TOOL_DISABLED"


@pytest.mark.asyncio
async def test_execute_high_risk_creates_pending_task_and_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    service = ToolService(
        session,
        ToolProxy(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))),
    )
    captured: dict[str, object] = {}

    def fake_enqueue(payload: dict[str, object], queue: object = None) -> None:
        captured.update(payload)

    monkeypatch.setattr("app.services.tool_service.enqueue_tool_approval", fake_enqueue)

    user = _make_user()
    tool = _make_tool(risk_level="high")
    result = await service.execute(tool, {"a": 1}, requester=user, conversation_id="conv-1")

    assert result["status"] == "pending_approval"
    assert result["task_id"]
    task = session.add.call_args.args[0]
    assert isinstance(task, Task)
    assert task.type == "tool_approval"
    assert task.status == "pending"
    assert task.tenant_id == user.tenant_id
    assert task.creator_id == user.id
    assert task.payload["tool_id"] == tool.tool_id
    assert captured["tool_id"] == tool.tool_id
    assert captured["task_id"] == result["task_id"]


@pytest.mark.asyncio
async def test_execute_high_risk_keeps_real_params_in_approval_task() -> None:
    session = _make_session()
    service = ToolService(
        session,
        ToolProxy(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))),
    )
    tool = _make_tool(risk_level="high")

    await service.execute(
        tool, {"phone": "13800138000", "name": "张三"}, requester=_make_user()
    )

    task = session.add.call_args.args[0]
    # The task row is the approver's decision payload — real values, unmasked.
    assert task.payload["params"] == {"phone": "13800138000", "name": "张三"}


@pytest.mark.asyncio
async def test_execute_confirm_mode_creates_pending_task() -> None:
    session = _make_session()
    service = ToolService(
        session,
        ToolProxy(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))),
    )
    tool = _make_tool(risk_level="low", permission_mode="confirm")

    result = await service.execute(tool, {}, requester=_make_user())

    assert result["status"] == "pending_approval"
    assert isinstance(session.add.call_args.args[0], Task)


# -- debug -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_disabled_raises_409() -> None:
    service, _ = _service()
    tool = _make_tool(permission_mode="disabled")

    with pytest.raises(AppError) as exc:
        await service.debug(tool, DebugToolDto(params={}))
    assert exc.value.status_code == 409
    assert exc.value.code == "TOOL_DISABLED"


@pytest.mark.asyncio
async def test_debug_returns_status_body_latency() -> None:
    service, _ = _service()
    tool = _make_tool()

    result = await service.debug(tool, DebugToolDto(params={"a": 1}))

    assert result["statusCode"] == 200
    assert result["responseBody"] == {"ok": True}
    assert result["latencyMs"] >= 0
    assert result["savedCaseId"] is None


@pytest.mark.asyncio
async def test_debug_saves_case_when_requested() -> None:
    session = _make_session()
    service = ToolService(
        session,
        ToolProxy(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))),
    )
    tool = _make_tool()

    result = await service.debug(
        tool, DebugToolDto(params={"a": 1}, save_as_test_case=True, name="case-1")
    )

    assert result["savedCaseId"]
    case = session.add.call_args.args[0]
    assert isinstance(case, ToolDebugCase)
    assert case.tool_id == tool.tool_id
    assert case.name == "case-1"
    assert case.status_code == 200


@pytest.mark.asyncio
async def test_debug_saves_masked_request_body() -> None:
    session = _make_session()
    service = ToolService(
        session,
        ToolProxy(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))),
    )
    tool = _make_tool()

    await service.debug(
        tool, DebugToolDto(params={"phone": "13800138000"}, save_as_test_case=True)
    )

    case = session.add.call_args.args[0]
    assert json.loads(case.request_body) == {"phone": "***"}
