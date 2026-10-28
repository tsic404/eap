"""Seed one pending tool-approval task that points at a controlled upstream.

Acceptance evidence for the approval chain must not depend on Dify opening a
conversation: this fixture inserts the tool and the pending task that a chat
turn with a gated tool would create, so a reviewer can approve it and watch the
task reach ``completed`` carrying the upstream's response. The task itself is
created by ``ToolService.execute`` — the same call the chat path makes — so its
priority, deadline and payload shape are the product's, not the fixture's.

Run inside the backend container (DATABASE_URL and REDIS_URL are in its
environment):

    python -m scripts.seed_tool_approval --endpoint http://backend:3001/api/health/live

The command prints one JSON line with the task identifier. ``--expires-in-minutes``
shifts the deadline without touching the product rule (the scan cancels whatever
is past due), which is how the 24-hour expiry clause is exercised without waiting
a day.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.folding import fold
from app.db import async_session_factory
from app.logging_conf import configure_logging, get_logger
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.tool import ToolRegistry
from app.models.user import User
from app.services.tool_proxy import ToolProxy
from app.services.tool_service import ToolService

log = get_logger(__name__)

_EXIT_OK = 0
_EXIT_NOT_FOUND = 2

DEFAULT_TENANT_SLUG = "acme"
DEFAULT_REQUESTER = "alice@acme.com"
DEFAULT_TOOL_ID = "qa-approval-probe"


async def seed(
    settings: Settings,
    *,
    tenant_slug: str,
    requester_email: str,
    tool_id: str,
    tool_name: str,
    endpoint: str,
    method: str,
    params: dict[str, Any],
    expires_in_minutes: int | None,
) -> dict[str, Any]:
    """Create the gated tool and its pending task; return the identifiers."""
    async with async_session_factory() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == tenant_slug))
        if tenant is None:
            raise LookupError(f"tenant {tenant_slug!r} not found")
        requester = await session.scalar(
            select(User).where(
                User.tenant_id == tenant.id, User.email_folded == fold(requester_email)
            )
        )
        if requester is None:
            raise LookupError(f"user {requester_email!r} not found in {tenant_slug!r}")

        tool = await _upsert_tool(
            session,
            tenant=tenant,
            requester=requester,
            tool_id=tool_id,
            tool_name=tool_name,
            endpoint=endpoint,
            method=method,
        )
        # Gated on risk/approval mode, so this returns a pending task instead of
        # calling the upstream — the gate decision stays the product's.
        outcome = await ToolService(session, ToolProxy()).execute(
            tool, params, requester=requester, conversation_id=None
        )
        if outcome.get("status") != "pending_approval":
            raise RuntimeError(f"tool {tool_id!r} is not gated: {outcome.get('status')}")

        task_id = uuid.UUID(str(outcome["task_id"]))
        if expires_in_minutes is not None:
            await _shift_deadline(session, task_id, expires_in_minutes)
        task = await session.get(Task, task_id)
        assert task is not None  # just written in this transaction
        return {
            "taskId": str(task.id),
            "toolId": tool.tool_id,
            "endpoint": tool.endpoint,
            "status": task.status,
            "expiresAt": task.expires_at.isoformat() if task.expires_at else None,
        }


async def _upsert_tool(
    session: AsyncSession,
    *,
    tenant: Tenant,
    requester: User,
    tool_id: str,
    tool_name: str,
    endpoint: str,
    method: str,
) -> ToolRegistry:
    """Keep the fixture's tool pointed at the requested upstream across re-runs."""
    tool = await session.get(ToolRegistry, tool_id)
    if tool is None:
        tool = ToolRegistry(
            tool_id=tool_id,
            tenant_id=tenant.id,
            name=tool_name,
            type="http",
            endpoint=endpoint,
            method=method,
            risk_level="high",
            permission_mode="confirm",
            auth_type="none",
            timeout_ms=5000,
            retry_policy={"max_retries": 0, "base_delay_ms": 100},
            status="active",
            created_by=requester.id,
        )
        session.add(tool)
    else:
        tool.endpoint = endpoint
        tool.method = method
        tool.risk_level = "high"
        tool.permission_mode = "confirm"
        tool.status = "active"
    await session.commit()
    return tool


async def _shift_deadline(session: AsyncSession, task_id: uuid.UUID, minutes: int) -> None:
    """Move the task's deadline by ``minutes`` from now (negative = already due)."""
    await session.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(expires_at=datetime.now(UTC) + timedelta(minutes=minutes))
        .execution_options(synchronize_session=False)
    )
    await session.commit()


def _parse_params(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("--params must be a JSON object")
    return parsed


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="seed_tool_approval",
        description="Seed one pending tool-approval task pointing at a controlled upstream.",
        epilog=(
            "Run as `python -m scripts.seed_tool_approval` from apps/backend (or /app in the "
            "backend container). Prints one JSON line with the task identifier."
        ),
    )
    parser.add_argument("--endpoint", required=True, help="upstream URL the tool calls")
    parser.add_argument("--method", default="GET", help="HTTP method (default: GET)")
    parser.add_argument("--tenant", default=DEFAULT_TENANT_SLUG, help="tenant slug (default: acme)")
    parser.add_argument(
        "--requester",
        default=DEFAULT_REQUESTER,
        help=f"task owner's email (default: {DEFAULT_REQUESTER})",
    )
    parser.add_argument("--tool-id", default=DEFAULT_TOOL_ID, help="tool identifier")
    parser.add_argument("--tool-name", default="QA approval probe", help="tool display name")
    parser.add_argument(
        "--params", default="{}", help="JSON object sent to the upstream (default: {})"
    )
    parser.add_argument(
        "--expires-in-minutes",
        type=int,
        default=None,
        help="override the deadline, relative to now (negative = already due)",
    )
    return parser.parse_args(argv)


async def _run(settings: Settings, args: argparse.Namespace) -> int:
    try:
        params = _parse_params(args.params)
    except ValueError as exc:
        log.error("seed_invalid_params", error=str(exc))
        return _EXIT_NOT_FOUND
    try:
        summary = await seed(
            settings,
            tenant_slug=args.tenant,
            requester_email=args.requester,
            tool_id=args.tool_id,
            tool_name=args.tool_name,
            endpoint=args.endpoint,
            method=args.method,
            params=params,
            expires_in_minutes=args.expires_in_minutes,
        )
    except (LookupError, RuntimeError) as exc:
        log.error("seed_failed", error=str(exc))
        return _EXIT_NOT_FOUND
    print(json.dumps(summary, ensure_ascii=False))
    return _EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    return asyncio.run(_run(settings, args))


if __name__ == "__main__":
    sys.exit(main())
