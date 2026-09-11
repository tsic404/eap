"""EAP SQLAlchemy models.

Importing this package registers every model on :data:`app.models.base.Base`'s
metadata, which is required for Alembic autogenerate to see the full schema.
"""

from app.models.agent import (
    AgentDailyStat,
    AgentKnowledgeBinding,
    AgentRegistry,
    AgentToolBinding,
)
from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.knowledge import KnowledgeBaseRegistry
from app.models.refresh_token import RefreshToken
from app.models.run_log import RunLog, TraceCitation, TraceStep, TraceToolCall
from app.models.task import Task
from app.models.task_outbox import TaskOutboxEvent
from app.models.tenant import Tenant
from app.models.tool import ToolDebugCase, ToolRegistry
from app.models.user import User
from app.models.user_memory import UserMemory

__all__ = [
    "AgentDailyStat",
    "AgentKnowledgeBinding",
    "AgentRegistry",
    "AgentToolBinding",
    "AuditLog",
    "Base",
    "KnowledgeBaseRegistry",
    "RefreshToken",
    "RunLog",
    "Task",
    "TaskOutboxEvent",
    "Tenant",
    "ToolDebugCase",
    "ToolRegistry",
    "TraceCitation",
    "TraceStep",
    "TraceToolCall",
    "User",
    "UserMemory",
]
