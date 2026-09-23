"""AgentService: orchestration, Saga compensation, lifecycle events (§30.2 + §32.2.4)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dify_console import CreateAppParams, DifyConsoleClient, ModelConfig
from app.errors import AppError
from app.events.agent import AGENT_CREATED, AGENT_DELETED, AGENT_OFFLINE, AGENT_PUBLISHED
from app.events.bus import EventBus, bus
from app.models.agent import AgentKnowledgeBinding, AgentRegistry, AgentToolBinding
from app.repositories.agent_repository import AgentRepository
from app.schemas.agent import CreateAgentDto, UpdateAgentDto

log = structlog.get_logger(__name__)

# EAP agent ``type`` → Dify app ``mode`` (§32.2.4).
_TYPE_TO_DIFY_MODE: dict[str, str] = {
    "chat": "chat",
    "agent": "agent-chat",
    "workflow": "workflow",
    "data": "chat",
}

_DUPLICATE_AGENT_CONSTRAINTS = frozenset({"pk_agent_registry", "uq_agent_registry_tenant_agent_id"})

# ``offline`` is the only state that must not be publishable: it is the
# deliberate "taken down" state, so a publish there is a state conflict rather
# than a missing resource. Unknown future states fail closed.
_PUBLISHABLE_STATUSES = frozenset({"draft", "testing", "published"})

# camelCase DTO keys that differ from the ORM column name on ``AgentRegistry``.
# ``update`` maps these before handing the payload to ``update_with_version`` so
# SQLAlchemy never sees an unmapped attribute (e.g. ``modelId`` → ``model_id``).
_FIELD_TO_COLUMN: dict[str, str] = {
    "modelId": "model_id",
    "modelName": "model_name",
    "modelProvider": "model_provider",
}


class AgentService:
    """Application service for the agent lifecycle."""

    def __init__(
        self,
        session: AsyncSession,
        console: DifyConsoleClient,
        event_bus: EventBus = bus,
    ) -> None:
        self._repo = AgentRepository(session)
        self._session = session
        self._console = console
        self._bus = event_bus

    async def _emit(self, name: str, **payload: object) -> None:
        # Events are best-effort: the DB mutation is already committed, so a
        # failing subscriber must not fail the request and trigger a retry that
        # repeats the (external) side effects.
        try:
            await self._bus.emit(name, **payload)
        except Exception:
            log.warning("agent_event_emit_failed", event=name, exc_info=True)

    async def register(
        self, dto: CreateAgentDto, *, tenant_id: uuid.UUID, actor_id: uuid.UUID
    ) -> AgentRegistry:
        """Create the Dify app/model/API key, then persist the agent (Saga).

        Any failure after the Dify app is created compensates by deleting that
        app, so a failed registration never leaves an orphaned Dify app behind.
        """
        dify_app_id: str | None = None
        try:
            app = await self._console.create_app(
                CreateAppParams(
                    name=dto.name,
                    mode=_TYPE_TO_DIFY_MODE[dto.type],
                    description=dto.description,
                    icon=dto.icon,
                )
            )
            dify_app_id = _require_id(app, "app")

            # A draft agent may be registered without a model (configured later
            # via PATCH); only configure the Dify app when a model was supplied.
            if dto.modelProvider is not None and dto.modelId is not None:
                await self._console.configure_model(
                    dify_app_id,
                    ModelConfig(
                        provider=dto.modelProvider,
                        model=dto.modelId,
                        opening_statement=dto.prompt,
                    ),
                )

            api_key = await self._console.create_api_key(dify_app_id)

            agent = AgentRegistry(
                agent_id=dto.agentId,
                tenant_id=tenant_id,
                dify_app_id=dify_app_id,
                dify_api_key=_extract_api_key(api_key),
                name=dto.name,
                description=dto.description,
                type=dto.type,
                category=dto.category,
                icon=dto.icon,
                tags=dto.tags,
                status="draft",
                model_id=dto.modelId,
                model_name=dto.modelName,
                model_provider=dto.modelProvider,
                prompt=dto.prompt,
                version=1,
                created_by=actor_id,
            )
            await self._repo.create(agent)
            self._bind(agent, dto.knowledgeBaseIds, dto.toolIds)
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if dify_app_id is not None:
                await _delete_app_best_effort(self._console, dify_app_id)
            mapped = _map_integrity_error(exc)
            if mapped is not None:
                raise mapped from exc
            raise
        except Exception:
            await self._session.rollback()
            if dify_app_id is not None:
                await _delete_app_best_effort(self._console, dify_app_id)
            raise

        await self._emit(
            AGENT_CREATED,
            agent_id=agent.agent_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            status=agent.status,
        )
        return agent

    async def update(
        self, agent_id: str, dto: UpdateAgentDto, *, tenant_id: uuid.UUID, actor_id: uuid.UUID
    ) -> AgentRegistry:
        agent = await self._repo.find_by_id(tenant_id, agent_id)
        if agent is None:
            raise AppError(404, "NOT_FOUND", "Agent not found")

        data = dto.model_dump(exclude_unset=True)
        version = int(data.pop("version"))
        kb_ids = data.pop("knowledgeBaseIds", None)
        tool_ids = data.pop("toolIds", None)
        fields = {_FIELD_TO_COLUMN.get(key, key): value for key, value in data.items()}

        updated = await self._repo.update_with_version(tenant_id, agent_id, version, **fields)
        if updated is None:
            await self._session.rollback()
            raise AppError(409, "CONFLICT", "Agent version conflict")

        try:
            await self._replace_bindings(updated, kb_ids, tool_ids)
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            mapped = _map_integrity_error(exc)
            if mapped is not None:
                raise mapped from exc
            raise
        return updated

    async def publish(
        self, agent_id: str, version: int, *, tenant_id: uuid.UUID, actor_id: uuid.UUID
    ) -> AgentRegistry:
        agent = await self._repo.find_by_id(tenant_id, agent_id)
        if agent is None:
            raise AppError(404, "NOT_FOUND", "Agent not found")

        # A missing/archived id is a 404 (above); an existing agent whose state
        # forbids publish is a 409 state conflict, so clients can tell them apart.
        if agent.status not in _PUBLISHABLE_STATUSES:
            raise AppError(
                409,
                "INVALID_STATE",
                f"Agent in status '{agent.status}' cannot be published",
            )

        updated = await self._repo.update_with_version(
            tenant_id,
            agent_id,
            version,
            status="published",
            published_at=datetime.now(UTC),
            published_by=actor_id,
        )
        if updated is None:
            await self._session.rollback()
            raise AppError(409, "CONFLICT", "Agent version conflict")

        await self._session.commit()
        await self._emit(
            AGENT_PUBLISHED,
            agent_id=agent_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            status="published",
        )
        return updated

    async def offline(
        self, agent_id: str, *, tenant_id: uuid.UUID, actor_id: uuid.UUID
    ) -> AgentRegistry:
        agent = await self._repo.find_by_id(tenant_id, agent_id)
        if agent is None:
            raise AppError(404, "NOT_FOUND", "Agent not found")

        updated = await self._repo.update_with_version(
            tenant_id, agent_id, agent.version, status="offline"
        )
        if updated is None:
            await self._session.rollback()
            raise AppError(409, "CONFLICT", "Agent version conflict")

        await self._session.commit()
        await self._emit(
            AGENT_OFFLINE,
            agent_id=agent_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            status="offline",
        )
        return updated

    async def delete(self, agent_id: str, *, tenant_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        agent = await self._repo.find_by_id(tenant_id, agent_id)
        if agent is None:
            raise AppError(404, "NOT_FOUND", "Agent not found")

        deleted = await self._repo.soft_delete(tenant_id, agent_id)
        if not deleted:
            raise AppError(404, "NOT_FOUND", "Agent not found")

        await self._session.commit()
        await self._emit(
            AGENT_DELETED,
            agent_id=agent_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
        # Remove the Dify app (and its service API key) so a soft-deleted agent
        # is no longer callable externally. Best-effort, like the Saga rollback.
        await _delete_app_best_effort(self._console, agent.dify_app_id)

    def _bind(self, agent: AgentRegistry, kb_ids: list[str], tool_ids: list[str]) -> None:
        for kb_id in kb_ids:
            agent.knowledge_bindings.append(
                AgentKnowledgeBinding(agent_id=agent.agent_id, kb_id=kb_id)
            )
        for tool_id in tool_ids:
            agent.tool_bindings.append(AgentToolBinding(agent_id=agent.agent_id, tool_id=tool_id))

    async def _replace_bindings(
        self,
        agent: AgentRegistry,
        kb_ids: list[str] | None,
        tool_ids: list[str] | None,
    ) -> None:
        if kb_ids is not None:
            await self._session.execute(
                delete(AgentKnowledgeBinding).where(
                    AgentKnowledgeBinding.agent_id == agent.agent_id
                )
            )
            for kb_id in kb_ids:
                self._session.add(AgentKnowledgeBinding(agent_id=agent.agent_id, kb_id=kb_id))
        if tool_ids is not None:
            await self._session.execute(
                delete(AgentToolBinding).where(AgentToolBinding.agent_id == agent.agent_id)
            )
            for tool_id in tool_ids:
                self._session.add(AgentToolBinding(agent_id=agent.agent_id, tool_id=tool_id))


def _require_id(response: object, label: str) -> str:
    """Extract the Dify resource id from a console response, or fail with 502."""
    if isinstance(response, dict):
        value = response.get("id")
        if value is not None:
            return str(value)
        nested = response.get("data")
        if isinstance(nested, dict) and nested.get("id") is not None:
            return str(nested["id"])
    raise AppError(502, "DIFY_ERROR", f"Dify did not return a {label} id")


def _extract_api_key(response: object) -> str | None:
    if isinstance(response, dict):
        token = response.get("token")
        if token is not None:
            return str(token)
        nested = response.get("data")
        if isinstance(nested, dict) and nested.get("token") is not None:
            return str(nested["token"])
    return None


def _map_integrity_error(exc: IntegrityError) -> AppError | None:
    """Map a known client-input IntegrityError to a 4xx; ``None`` for unknown."""
    constraint = _constraint_name(exc)
    if constraint in _DUPLICATE_AGENT_CONSTRAINTS:
        return AppError(409, "DUPLICATE_AGENT_ID", "agentId already exists")
    if constraint is not None and constraint.startswith("fk_"):
        # A binding references a knowledge base or tool id that does not exist.
        return AppError(400, "INVALID_BINDING", "Unknown knowledge base or tool id")
    return None


_CONSTRAINT_RE = re.compile(r'constraint "([^"]+)"')


def _constraint_name(exc: IntegrityError) -> str | None:
    """Extract the violated constraint name from the driver exception.

    asyncpg exposes ``constraint_name`` on its exceptions, but not every driver
    does; fall back to parsing the server message so mapping stays portable.
    """
    name = getattr(exc.orig, "constraint_name", None)
    if isinstance(name, str) and name:
        return name
    match = _CONSTRAINT_RE.search(str(exc.orig if exc.orig is not None else exc))
    return match.group(1) if match is not None else None


async def _delete_app_best_effort(console: DifyConsoleClient, app_id: str) -> None:
    try:
        await console.delete_app(app_id)
    except Exception:
        # Compensation is best-effort: a failed cleanup must not mask the
        # original registration error.
        log.warning("agent_saga_compensation_failed", dify_app_id=app_id, exc_info=True)
