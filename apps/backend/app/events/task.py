"""Task status-change domain event and emission helper."""

from __future__ import annotations

from dataclasses import dataclass

from app.events.bus import bus

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
