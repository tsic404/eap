"""Knowledge-base domain events.

Event names are stable, machine-readable strings consumed by subscribers
(e.g. audit, outbox, external integrations). Payloads are keyword args passed
through :meth:`app.events.bus.EventBus.emit` unchanged.
"""

from __future__ import annotations

KB_CREATED_EVENT = "kb.created"
DOCUMENT_UPLOADED_EVENT = "document.uploaded"
DOCUMENT_INDEXED_EVENT = "document.indexed"
DOCUMENT_INDEX_FAILED_EVENT = "document.index_failed"
KB_DELETED_EVENT = "kb.deleted"
