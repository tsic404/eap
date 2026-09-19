/**
 * Run-log / trace domain types mirroring the backend `app/schemas/run_log.py`
 * DTOs (`RunLogDto` / `RunLogDetailDto`). Fields are camelCase to match the
 * wire format produced by `_to_dto` / `_to_detail_dto`.
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

/** One workflow step on a trace's timeline. */
export interface TraceStep {
  stepOrder: number;
  name: string;
  type: string | null;
  status: string | null;
  latencyMs: number | null;
  detail: string | null;
}

/** One knowledge-base hit cited by the run. */
export interface TraceCitation {
  sourceName: string | null;
  kbName: string | null;
  excerpt: string | null;
  score: number | null;
}

/** One tool invocation made during the run. */
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
