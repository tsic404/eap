/**
 * Agent domain types mirroring the backend `app/schemas/agent.py` DTOs.
 *
 * The backend serializes agents to camelCase (see `agent_to_dto`), so these
 * shapes match the wire format exactly. Tool list responses are the exception:
 * `GET /api/tools` returns `ToolRead` objects with snake_case keys.
 */

export const AGENT_TYPES = ["chat", "agent", "workflow", "data"] as const;
export type AgentType = (typeof AGENT_TYPES)[number];

export const AGENT_STATUSES = ["draft", "testing", "published", "offline"] as const;
export type AgentStatus = (typeof AGENT_STATUSES)[number];

/** List item returned by `GET /api/agents` and `GET /api/agents/{id}`. */
export interface Agent {
  agentId: string;
  tenantId: string;
  difyAppId: string;
  name: string;
  description: string | null;
  type: string;
  category: string | null;
  icon: string | null;
  tags: string[];
  status: string;
  modelId: string | null;
  modelName: string | null;
  modelProvider: string | null;
  prompt: string | null;
  version: number;
  createdBy: string | null;
  publishedAt: string | null;
  publishedBy: string | null;
  createdAt: string;
  updatedAt: string;
}

/** Paginated envelope returned by `GET /api/agents`. */
export interface AgentListResponse {
  items: Agent[];
  total: number;
  page: number;
  pageSize: number;
}

export interface KnowledgeBinding {
  kbId: string;
  name: string | null;
}

export interface ToolBinding {
  toolId: string;
  name: string | null;
}

export interface RunLog {
  traceId: string;
  status: string | null;
  input: string | null;
  latencyMs: number | null;
  createdAt: string | null;
}

/** `GET /api/agents/{id}` response — an agent plus its bindings and run logs. */
export interface AgentDetail extends Agent {
  boundKnowledge: KnowledgeBinding[];
  boundTools: ToolBinding[];
  recentLogs: RunLog[];
}

/** Query parameters accepted by `GET /api/agents`. */
export interface AgentListParams {
  page?: number;
  pageSize?: number;
  status?: string;
  category?: string;
  type?: string;
  search?: string;
}

/** Body of `POST /api/agents`. */
export interface CreateAgentInput {
  agentId: string;
  name: string;
  description?: string | null;
  type: AgentType;
  category?: string | null;
  icon?: string | null;
  tags?: string[];
  modelId?: string | null;
  modelName?: string | null;
  modelProvider?: string | null;
  prompt?: string | null;
  knowledgeBaseIds?: string[];
  toolIds?: string[];
}

/** Body of `PATCH /api/agents/{id}`; `version` enables optimistic locking. */
export interface UpdateAgentInput {
  version: number;
  name?: string;
  description?: string | null;
  category?: string | null;
  icon?: string | null;
  tags?: string[];
  modelId?: string | null;
  modelName?: string | null;
  modelProvider?: string | null;
  prompt?: string | null;
  knowledgeBaseIds?: string[];
  toolIds?: string[];
}

/** Subset of `ToolRead` needed by the resource-binding step. */
export interface Tool {
  tool_id: string;
  name: string;
  description: string | null;
  type: string;
  status: string;
}
