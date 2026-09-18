"""Event bus and domain events."""

from app.events.agent import (
    AGENT_CREATED,
    AGENT_DELETED,
    AGENT_OFFLINE,
    AGENT_PUBLISHED,
)
from app.events.audit import AUDIT_LOG_EVENT, AuditLogEvent, emit_audit_log
from app.events.bus import EventBus, bus
from app.events.knowledge import (
    DOCUMENT_INDEX_FAILED_EVENT,
    DOCUMENT_INDEXED_EVENT,
    DOCUMENT_UPLOADED_EVENT,
    KB_CREATED_EVENT,
    KB_DELETED_EVENT,
)
from app.events.task import (
    TASK_STATUS_CHANGED_EVENT,
    TaskStatusChangedEvent,
    emit_task_status_changed,
)

__all__ = [
    "AGENT_CREATED",
    "AGENT_DELETED",
    "AGENT_OFFLINE",
    "AGENT_PUBLISHED",
    "AUDIT_LOG_EVENT",
    "AuditLogEvent",
    "DOCUMENT_INDEXED_EVENT",
    "DOCUMENT_INDEX_FAILED_EVENT",
    "DOCUMENT_UPLOADED_EVENT",
    "EventBus",
    "KB_CREATED_EVENT",
    "KB_DELETED_EVENT",
    "TASK_STATUS_CHANGED_EVENT",
    "TaskStatusChangedEvent",
    "bus",
    "emit_audit_log",
    "emit_task_status_changed",
]
