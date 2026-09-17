"""RQ worker job: dispatch a high-risk tool approval request to approvers.

Approver notification in P1 is frontend polling of pending tasks (§31.2.5);
this job records the dispatch in the audit trail so every approval request has
a durable, queryable ledger entry.
"""

from __future__ import annotations

from typing import Any

from app.queue import enqueue_audit_log


def dispatch_approval(payload: dict[str, Any]) -> None:
    """RQ entrypoint: write an audit entry for a tool-approval request."""
    enqueue_audit_log(
        {
            "tenant_id": payload.get("tenant_id"),
            "action": "tool.approval.requested",
            "resource": "task",
            "resource_id": payload.get("task_id"),
            "user_id": payload.get("requester_id"),
            "details": payload,
        }
    )
