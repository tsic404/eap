"""Agent module request/response DTOs and serialization helpers (§32.2.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.models.agent import AgentRegistry
from app.models.run_log import RunLog

AgentType = Literal["chat", "agent", "workflow", "data"]

# Lowercase letters, digits and hyphens, 3–64 chars, no leading/trailing hyphen.
_AGENT_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$"


class CreateAgentDto(BaseModel):
    """Body of ``POST /api/agents``."""

    agentId: str = Field(pattern=_AGENT_ID_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    type: AgentType = "chat"
    category: str | None = Field(default=None, max_length=100)
    icon: str | None = Field(default="🤖", max_length=50)
    tags: list[str] = Field(default_factory=list, max_length=10)
    modelId: str | None = Field(default=None, max_length=255)
    modelName: str | None = Field(default=None, max_length=255)
    modelProvider: str | None = Field(default=None, max_length=255)
    prompt: str | None = None
    knowledgeBaseIds: list[str] = Field(default_factory=list)
    toolIds: list[str] = Field(default_factory=list)


class UpdateAgentDto(BaseModel):
    """Body of ``PATCH /api/agents/{id}``; ``version`` enables optimistic locking."""

    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=100)
    icon: str | None = Field(default=None, max_length=50)
    tags: list[str] | None = Field(default=None, max_length=10)
    modelId: str | None = Field(default=None, max_length=255)
    modelName: str | None = Field(default=None, max_length=255)
    modelProvider: str | None = Field(default=None, max_length=255)
    prompt: str | None = None
    knowledgeBaseIds: list[str] | None = None
    toolIds: list[str] | None = None

    @field_validator("name", "tags")
    @classmethod
    def _reject_explicit_null(cls, value: Any) -> Any:
        # ``name``/``tags`` map to NOT NULL columns; an explicit ``null`` would
        # reach ``update().values(...)`` and violate the constraint. ``None`` is
        # only valid as the "field omitted" default (validators skip defaults).
        if value is None:
            raise ValueError("must not be null")
        return value


class PublishAgentDto(BaseModel):
    """Body of ``POST /api/agents/{id}/publish``; ``version`` enables optimistic locking."""

    version: int = Field(ge=1)


class KnowledgeBindingDto(BaseModel):
    kbId: str
    name: str | None = None


class ToolBindingDto(BaseModel):
    toolId: str
    name: str | None = None


class RunLogDto(BaseModel):
    traceId: str
    status: str | None = None
    input: str | None = None
    latencyMs: int | None = None
    createdAt: datetime | None = None


class AgentDto(BaseModel):
    agentId: str
    tenantId: str
    difyAppId: str
    name: str
    description: str | None
    type: str
    category: str | None
    icon: str | None
    tags: list[str]
    status: str
    modelId: str | None
    modelName: str | None
    modelProvider: str | None
    prompt: str | None
    version: int
    createdBy: str | None
    publishedAt: datetime | None
    publishedBy: str | None
    createdAt: datetime
    updatedAt: datetime


class AgentDetailDto(AgentDto):
    boundKnowledge: list[KnowledgeBindingDto]
    boundTools: list[ToolBindingDto]
    recentLogs: list[RunLogDto]


def agent_to_dto(agent: AgentRegistry) -> AgentDto:
    """Serialize an ``AgentRegistry`` row to the public camelCase DTO."""
    return AgentDto(
        agentId=agent.agent_id,
        tenantId=str(agent.tenant_id),
        difyAppId=agent.dify_app_id,
        name=agent.name,
        description=agent.description,
        type=agent.type,
        category=agent.category,
        icon=agent.icon,
        tags=agent.tags or [],
        status=agent.status,
        modelId=agent.model_id,
        modelName=agent.model_name,
        modelProvider=agent.model_provider,
        prompt=agent.prompt,
        version=agent.version,
        createdBy=str(agent.created_by) if agent.created_by is not None else None,
        publishedAt=agent.published_at,
        publishedBy=str(agent.published_by) if agent.published_by is not None else None,
        createdAt=agent.created_at,
        updatedAt=agent.updated_at,
    )


def agent_to_detail_dto(agent: AgentRegistry, recent_logs: list[RunLog]) -> AgentDetailDto:
    """Serialize an agent with its bound knowledge/tools and recent run logs."""
    return AgentDetailDto(
        **agent_to_dto(agent).model_dump(),
        boundKnowledge=[
            KnowledgeBindingDto(
                kbId=binding.kb_id,
                name=binding.knowledge.name if binding.knowledge is not None else None,
            )
            for binding in agent.knowledge_bindings
        ],
        boundTools=[
            ToolBindingDto(
                toolId=binding.tool_id,
                name=binding.tool.name if binding.tool is not None else None,
            )
            for binding in agent.tool_bindings
        ],
        recentLogs=[
            RunLogDto(
                traceId=log.trace_id,
                status=log.status,
                input=log.input,
                latencyMs=log.latency_ms,
                createdAt=log.created_at,
            )
            for log in recent_logs
        ],
    )
