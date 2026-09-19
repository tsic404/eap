/**
 * Run-log / trace domain types mirroring the backend run_logs DTOs.
 *
 * Run-log resources are serialized to camelCase, consistent with the agent
 * DTOs whose `recentLogs` already expose `traceId`/`latencyMs`/`createdAt`.
 */

export const RUN_LOG_STATUSES = ["success", "failed", "running", "blocked"] as const;
export type RunLogStatus = (typeof RUN_LOG_STATUSES)[number];

/** One row of `GET /api/run-logs` — the audit summary, no trace sub-records. */
export interface RunLogSummary {
  traceId: string;
  conversationId: string | null;
  agentId: string;
  agentName: string | null;
  userId: string;
  userName: string | null;
  status: RunLogStatus | null;
  input: string | null;
  modelName: string | null;
  tokenUsage: number | null;
  latencyMs: number | null;
  toolCallCount: number;
  knowledgeHitCount: number;
  createdAt: string;
}

/** Cursor-paginated envelope returned by `GET /api/run-logs`. */
export interface RunLogListResponse {
  items: RunLogSummary[];
  nextCursor: string | null;
}

/** Query parameters accepted by `GET /api/run-logs` (§32.6 list contract). */
export interface RunLogListParams {
  agentId?: string;
  conversationId?: string;
  status?: RunLogStatus;
  from?: string;
  to?: string;
  cursor?: string;
  limit?: number;
}

/** Filter-bar state; empty strings mean "no filter" for that dimension. */
export interface RunLogFilters {
  agentId: string;
  conversationId: string;
  status: "" | RunLogStatus;
  from: string;
  to: string;
}

export const EMPTY_RUN_LOG_FILTERS: RunLogFilters = {
  agentId: "",
  conversationId: "",
  status: "",
  from: "",
  to: "",
};

/** One trace step rendered by TraceTimeline. */
export interface TraceStep {
  stepOrder: number;
  name: string;
  type: string | null;
  status: string | null;
  latencyMs: number | null;
  detail: string | null;
}

/** One recalled chunk shown in the citations view. */
export interface TraceCitation {
  sourceName: string | null;
  kbName: string | null;
  excerpt: string | null;
  score: number | null;
}

/** One tool invocation shown in the tool-calls view. */
export interface TraceToolCall {
  toolName: string | null;
  toolId: string | null;
  status: string | null;
  permissionMode: string | null;
  latencyMs: number | null;
  requestSummary: string | null;
  responseSummary: string | null;
}

/** Detail returned by `GET /api/run-logs/{traceId}` — summary + sub-records. */
export interface RunLogDetail extends RunLogSummary {
  output: string | null;
  steps: TraceStep[];
  citations: TraceCitation[];
  toolCalls: TraceToolCall[];
}
