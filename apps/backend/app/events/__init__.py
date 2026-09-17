"""Event bus and domain events."""

from app.events.audit import AUDIT_LOG_EVENT, AuditLogEvent, emit_audit_log
from app.events.bus import EventBus, bus
from app.events.knowledge import (
    DOCUMENT_INDEX_FAILED_EVENT,
    DOCUMENT_INDEXED_EVENT,
    DOCUMENT_UPLOADED_EVENT,
    KB_CREATED_EVENT,
    KB_DELETED_EVENT,
)

__all__ = [
    "AUDIT_LOG_EVENT",
    "AuditLogEvent",
    "DOCUMENT_INDEXED_EVENT",
    "DOCUMENT_INDEX_FAILED_EVENT",
    "DOCUMENT_UPLOADED_EVENT",
    "EventBus",
    "KB_CREATED_EVENT",
    "KB_DELETED_EVENT",
    "bus",
    "emit_audit_log",
]
