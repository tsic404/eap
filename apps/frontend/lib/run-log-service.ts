import { API_ROUTES } from "./api-routes";
import type { ApiEnvelope } from "./auth-types";
import { apiClient } from "./http-client";
import type { RunLogDetail } from "./run-log-types";

/** Fetch one run log's full trace (summary plus steps/citations/tool calls). */
export async function getRunLogDetail(traceId: string): Promise<RunLogDetail> {
  const response = await apiClient.get<ApiEnvelope<RunLogDetail>>(
    `${API_ROUTES.runLogs}/${traceId}`,
  );
  return response.data.data;
}
