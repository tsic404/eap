/**
 * Tool domain types mirroring the backend `app/schemas/tool.py` DTOs.
 *
 * `GET /api/tools` returns `ToolRead` objects serialized from Pydantic models,
 * so the keys stay snake_case (the agent list is the camelCase exception).
 */

export const TOOL_TYPES = ["http", "database", "rpa", "webhook", "mcp"] as const;
export type ToolType = (typeof TOOL_TYPES)[number];

export const RISK_LEVELS = ["low", "medium", "high"] as const;
export type RiskLevel = (typeof RISK_LEVELS)[number];

export const PERMISSION_MODES = ["auto", "confirm", "disabled"] as const;
export type PermissionMode = (typeof PERMISSION_MODES)[number];

export const TOOL_STATUSES = ["active", "disabled", "archived"] as const;
export type ToolStatus = (typeof TOOL_STATUSES)[number];

export const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;
export type HttpMethod = (typeof HTTP_METHODS)[number];

export const AUTH_TYPES = ["none", "api_key", "bearer", "basic", "oauth2"] as const;
export type AuthType = (typeof AUTH_TYPES)[number];

/** Full tool shape returned by `GET /api/tools` and `GET /api/tools/{id}`. */
export interface ToolDetail {
  tool_id: string;
  tenant_id: string | null;
  name: string;
  description: string | null;
  type: string;
  endpoint: string | null;
  method: string | null;
  risk_level: string;
  permission_mode: string;
  auth_type: string | null;
  auth_config: Record<string, unknown> | null;
  timeout_ms: number;
  retry_policy: Record<string, unknown> | null;
  circuit_breaker: Record<string, unknown> | null;
  status: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

/** Body of `POST /api/tools`. */
export interface CreateToolInput {
  name: string;
  tool_id: string;
  type: ToolType;
  description?: string | null;
  risk_level: RiskLevel;
  permission_mode: PermissionMode;
  endpoint: string;
  method: HttpMethod;
  auth_type: AuthType;
  auth_config?: Record<string, unknown> | null;
  timeout_ms: number;
  retry_policy?: { max_retries: number; base_delay_ms: number };
  circuit_breaker?: Record<string, unknown> | null;
}

/** Body of `PATCH /api/tools/{id}`; only provided fields are applied. */
export interface UpdateToolInput {
  name?: string;
  description?: string | null;
  type?: ToolType;
  risk_level?: RiskLevel;
  permission_mode?: PermissionMode;
  endpoint?: string;
  method?: HttpMethod;
  auth_type?: AuthType;
  auth_config?: Record<string, unknown> | null;
  timeout_ms?: number;
  retry_policy?: { max_retries: number; base_delay_ms: number };
  circuit_breaker?: Record<string, unknown> | null;
  status?: ToolStatus;
}

/** Body of `POST /api/tools/{id}/debug`. */
export interface DebugToolInput {
  params: Record<string, unknown>;
  save_as_test_case: boolean;
  name?: string | null;
}

/** Response of `POST /api/tools/{id}/debug`. */
export interface DebugToolResult {
  statusCode: number;
  responseBody: unknown;
  latencyMs: number;
  savedCaseId: string | null;
}
