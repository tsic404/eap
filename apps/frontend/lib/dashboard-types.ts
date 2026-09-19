/**
 * Dashboard domain types mirroring the backend `app/schemas/dashboard.py` and
 * `app/schemas/model.py` DTOs. Field names are camelCase to match the wire
 * contract; the backend wraps responses in the `{ code, data, message }`
 * envelope, so these shapes describe the `data` payload.
 */

import type { Agent } from "./agent-types";
import type { Conversation } from "./conversation-types";

/** Admin four-dimension metrics over the current tenant, scoped to today. */
export interface MetricSummary {
  totalAgents: number;
  todayCalls: number;
  avgLatencyMs: number | null;
  errorRate: number;
}

/** One hourly bucket of the rolling 24h call trend. */
export interface TrendPoint {
  hour: string;
  calls: number;
}

/** One agent in the top-usage ranking (ordered by today's call count). */
export interface TopAgent {
  agentId: string;
  agentName: string | null;
  calls: number;
}

/** Response of `GET /api/dashboard/admin`. */
export interface AdminDashboard {
  metrics: MetricSummary;
  trend: TrendPoint[];
  topAgents: TopAgent[];
}

/** Response of `GET /api/dashboard/user`. */
export interface UserHome {
  recommendedAgents: Agent[];
  recentConversations: Conversation[];
  pendingTaskCount: number;
}

/** One Dify model provider summary returned by `GET /api/models`. */
export interface ModelProvider {
  provider: string;
  label: string | null;
  deploymentType: string | null;
  modelCount: number;
}
