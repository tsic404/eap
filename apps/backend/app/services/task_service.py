"""TaskService: state machine, optimistic-lock transition, and transactional outbox.

Implements architecture §32.7.3 (state machine), §33.3 (optimistic lock + outbox)
and §34.2 (callback transaction + direct enqueue as the main path, outbox as the
crash-compensation fallback).

The transition reads the task inside one ``session.begin()`` block, applies a
conditional UPDATE keyed on the status it just read (optimistic lock), and
writes the outbox row in the same transaction — a conflict raises inside the
block, so the whole transaction (including any outbox row) rolls back.

Dispatch is idempotent: after commit the service atomically *claims* the outbox
row (``claim_outbox_row``), enqueues, and releases the claim on enqueue failure.
The claim — not RQ's ``job_id`` — is what stops the reconciler from re-enqueueing
the same event, so an approved task is never dispatched twice.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from sqlalchemy import CursorResult, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.events.task import TaskStatusChangedEvent, emit_task_status_changed
from app.models.task import Task
from app.models.task_outbox import TaskOutboxEvent
from app.models.user import User
from app.queue import enqueue_process_task, map_queue_name
from app.services.task_reconcile import claim_outbox_row

log = structlog.get_logger(__name__)

# Which statuses trigger worker execution (and therefore an outbox row + enqueue).
# Approving a pending task runs it; retrying a failed task re-runs it. Reject and
# cancel are terminal and carry no queue work.
_WORK_TRIGGERING_STATUSES = frozenset({"approved", "executing"})

# Role whitelist for admin-gated actions (approve/reject): a single source of
# truth imported by the controller (route gate) and enforced here (service
# defense-in-depth).
ADMIN_ROLES: tuple[str, ...] = ("platform_admin", "agent_admin")

VALID_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending": ("approved", "rejected", "cancelled"),
    "approved": ("executing", "cancelled"),
    "rejected": (),
    "executing": ("completed", "failed", "cancelled"),
    "completed": (),
    "failed": ("executing", "cancelled"),
    "cancelled": (),
}


def encode_cursor(created_at: datetime, task_id: uuid.UUID) -> str:
    """Encode a ``(created_at, id)`` keyset cursor into an opaque token."""
    raw = f"{created_at.isoformat()}|{task_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Decode a keyset cursor; raise 400 on a malformed token."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        timestamp, task_id = raw.split("|", 1)
        return datetime.fromisoformat(timestamp), uuid.UUID(task_id)
    except (ValueError, TypeError):
        raise AppError(400, "INVALID_CURSOR", "Invalid cursor") from None


class TaskService:
    """Business orchestration over the task state machine and its RQ handoff."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- Reads --------------------------------------------------------------

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        status_filter: str | None = None,
        type_filter: str | None = None,
        priority: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[Task], str | None]:
        """Return tasks for ``tenant_id`` (cursor pagination + filters)."""
        statement = select(Task).where(Task.tenant_id == tenant_id)
        if status_filter is not None:
            statement = statement.where(Task.status == status_filter)
        if type_filter is not None:
            statement = statement.where(Task.type == type_filter)
        if priority is not None:
            statement = statement.where(Task.priority == priority)
        if cursor is not None:
            cursor_at, cursor_id = decode_cursor(cursor)
            statement = statement.where(
                or_(
                    Task.created_at < cursor_at,
                    and_(Task.created_at == cursor_at, Task.id < cursor_id),
                )
            )
        statement = statement.order_by(Task.created_at.desc(), Task.id.desc()).limit(limit + 1)
        rows = list((await self.session.execute(statement)).scalars().all())
        items = rows[:limit]
        next_cursor = (
            encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > limit else None
        )
        return items, next_cursor

    async def get(self, task_id: str, tenant_id: uuid.UUID) -> Task:
        """Return one task, raising 404 when absent or cross-tenant."""
        task = await self._select_task(self._parse_task_id(task_id), tenant_id)
        if task is None:
            raise AppError(404, "TASK_NOT_FOUND", "Task not found")
        return task

    # -- Transitions --------------------------------------------------------

    async def transition(
        self,
        task_id: str,
        to_status: str,
        *,
        actor: User,
        tenant_id: uuid.UUID,
        metadata: str | None = None,
    ) -> Task:
        """Atomically move a task to ``to_status`` if the state machine allows it."""
        task_uuid = self._parse_task_id(task_id)

        # The router shares this session with the auth dependencies, which
        # autobegin a transaction before the handler runs, so ``begin()`` would
        # raise; use the existing transaction and commit/rollback explicitly.
        try:
            task = await self._select_task(task_uuid, tenant_id)
            if task is None:
                raise AppError(404, "TASK_NOT_FOUND", "Task not found")
            from_status = task.status
            if to_status not in VALID_TRANSITIONS.get(from_status, ()):
                raise AppError(
                    422,
                    "INVALID_TRANSITION",
                    f"Cannot transition from '{from_status}' to '{to_status}'",
                )
            if to_status == "approved" and self._is_expired(task):
                raise AppError(422, "TASK_EXPIRED", "Task has expired")
            if to_status in ("approved", "rejected") and actor.role not in ADMIN_ROLES:
                raise AppError(403, "FORBIDDEN", "Only an admin can approve or reject tasks")
            if to_status == "cancelled" and not self._is_owner_or_admin(task, actor):
                raise AppError(403, "FORBIDDEN", "Only the task owner or an admin can cancel")

            task_type = task.type
            task_payload = task.payload
            outbox_id = str(uuid.uuid4())

            update_values: dict[str, Any] = {"status": to_status}
            # A rejection is terminal; persist the reason so the requester can
            # see why without a separate approval-comment column.
            if to_status == "rejected" and metadata is not None:
                update_values["result"] = {"reason": metadata}

            result = cast(
                CursorResult[Any],
                await self.session.execute(
                    update(Task)
                    .where(Task.id == task_uuid, Task.status == from_status)
                    .values(**update_values)
                    .execution_options(synchronize_session=False)
                ),
            )
            if result.rowcount == 0:
                raise AppError(409, "TASK_ALREADY_PROCESSED", "Task was already processed")

            if to_status in _WORK_TRIGGERING_STATUSES:
                self.session.add(
                    TaskOutboxEvent(
                        id=outbox_id,
                        task_id=str(task_uuid),
                        type=map_queue_name(task_type),
                        status=to_status,
                        payload=task_payload,
                    )
                )

            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        if to_status in _WORK_TRIGGERING_STATUSES:
            await self._dispatch_after_commit(task_uuid, outbox_id, task_type, task_payload)

        await self._emit_status_changed(task_uuid, from_status, to_status, actor, metadata)

        refreshed = await self.session.get(Task, task_uuid, populate_existing=True)
        if refreshed is None:
            raise AppError(404, "TASK_NOT_FOUND", "Task not found")
        return refreshed

    async def retry(
        self,
        task_id: str,
        *,
        actor: User,
        tenant_id: uuid.UUID,
    ) -> Task:
        """Re-run a failed task (``failed -> executing``), respecting retry budget."""
        task_uuid = self._parse_task_id(task_id)

        try:
            task = await self._select_task(task_uuid, tenant_id)
            if task is None:
                raise AppError(404, "TASK_NOT_FOUND", "Task not found")
            if not self._is_owner_or_admin(task, actor):
                raise AppError(403, "FORBIDDEN", "Only the task owner or an admin can retry")
            if task.status != "failed":
                raise AppError(
                    422, "INVALID_TRANSITION", f"Cannot retry a task in '{task.status}' state"
                )
            if task.retry_count >= task.max_retries:
                raise AppError(409, "MAX_RETRIES_EXCEEDED", "Task has no retries remaining")

            task_type = task.type
            task_payload = task.payload
            outbox_id = str(uuid.uuid4())

            result = cast(
                CursorResult[Any],
                await self.session.execute(
                    update(Task)
                    .where(Task.id == task_uuid, Task.status == "failed")
                    .values(status="executing", retry_count=task.retry_count + 1)
                    .execution_options(synchronize_session=False)
                ),
            )
            if result.rowcount == 0:
                raise AppError(409, "TASK_ALREADY_PROCESSED", "Task was already processed")
            self.session.add(
                TaskOutboxEvent(
                    id=outbox_id,
                    task_id=str(task_uuid),
                    type=map_queue_name(task_type),
                    status="executing",
                    payload=task_payload,
                )
            )

            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        await self._dispatch_after_commit(task_uuid, outbox_id, task_type, task_payload)

        await self._emit_status_changed(task_uuid, "failed", "executing", actor, None)

        refreshed = await self.session.get(Task, task_uuid, populate_existing=True)
        if refreshed is None:
            raise AppError(404, "TASK_NOT_FOUND", "Task not found")
        return refreshed

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _parse_task_id(task_id: str) -> uuid.UUID:
        try:
            return uuid.UUID(task_id)
        except (ValueError, TypeError, AttributeError):
            raise AppError(404, "TASK_NOT_FOUND", "Task not found") from None

    @staticmethod
    def _is_expired(task: Task) -> bool:
        return task.expires_at is not None and task.expires_at <= datetime.now(UTC)

    @staticmethod
    def _is_owner_or_admin(task: Task, actor: User) -> bool:
        """True when ``actor`` may operate on ``task`` (owner, assignee, or admin)."""
        return (
            actor.role in ADMIN_ROLES or task.creator_id == actor.id or task.assignee_id == actor.id
        )

    @staticmethod
    async def _emit_status_changed(
        task_uuid: uuid.UUID,
        from_status: str,
        to_status: str,
        actor: User,
        metadata: str | None,
    ) -> None:
        try:
            await emit_task_status_changed(
                TaskStatusChangedEvent(
                    task_id=str(task_uuid),
                    from_status=from_status,
                    to_status=to_status,
                    actor_id=str(actor.id),
                    metadata=metadata,
                )
            )
        except Exception:
            # A subscriber failure must never fail the already-committed
            # transition; the state change is durable regardless.
            log.warning(
                "task_status_changed_emit_failed",
                task_id=str(task_uuid),
                to_status=to_status,
                exc_info=True,
            )

    async def _select_task(self, task_uuid: uuid.UUID, tenant_id: uuid.UUID) -> Task | None:
        result = await self.session.execute(
            select(Task).where(Task.id == task_uuid, Task.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def _dispatch_after_commit(
        self,
        task_uuid: uuid.UUID,
        outbox_id: str,
        task_type: str,
        task_payload: dict[str, Any],
    ) -> None:
        """Enqueue first, then claim the outbox row — no permanent-loss window.

        Enqueueing before the claim means a crash after a successful enqueue
        leaves the row undelivered and the reconciler re-enqueues it
        (at-least-once); the worker's atomic ``SETNX`` execution claim turns
        that duplicate into a no-op. The reverse order (claim then enqueue)
        would lose the task forever if the process died between claim-commit
        and enqueue, since the reconciler only scans ``delivered_at IS NULL``.
        """
        try:
            enqueue_process_task(
                task_id=str(task_uuid),
                outbox_id=outbox_id,
                queue_name=map_queue_name(task_type),
                payload=task_payload,
            )
        except Exception:
            # Enqueue failed: leave the outbox undelivered; the 15s reconciler
            # retries. No claim was taken, so the task is never lost.
            log.warning("task_enqueue_failed", task_id=str(task_uuid), exc_info=True)
            return
        # Enqueue succeeded: mark delivered. A failure here only risks a
        # duplicate re-enqueue, which the worker's SETNX claim dedups.
        await claim_outbox_row(self.session, outbox_id)
        await self.session.commit()
