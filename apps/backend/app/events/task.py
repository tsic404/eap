"""Task status-change domain event and emission helper."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.events.bus import bus

log = structlog.get_logger(__name__)

TASK_STATUS_CHANGED_EVENT = "task.status_changed"


@dataclass(frozen=True, slots=True)
class TaskStatusChangedEvent:
    """Snapshot of a task state transition.

    ``from_status`` is the pre-transition status (the transition reads the task
    before the optimistic-lock update, so this is the expected prior state).
    ``metadata`` carries an approver comment or reject reason when present.
    """

    task_id: str
    from_status: str
    to_status: str
    actor_id: str | None = None
    metadata: str | None = None


async def emit_task_status_changed(event: TaskStatusChangedEvent) -> None:
    """Publish a ``task.status_changed`` event to the process-wide bus."""
    await bus.emit(TASK_STATUS_CHANGED_EVENT, event=event)


async def emit_task_status_changed_best_effort(event: TaskStatusChangedEvent) -> None:
    """Publish a transition whose state change is already committed.

    Subscribers must never fail the caller: a listener failure is logged and
    swallowed, because the transition itself is durable either way.
    """
    try:
        await emit_task_status_changed(event)
    except Exception:
        log.warning(
            "task_status_changed_emit_failed",
            task_id=event.task_id,
            to_status=event.to_status,
            exc_info=True,
        )
