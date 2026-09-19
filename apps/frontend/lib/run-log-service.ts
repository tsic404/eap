/**
 * Run-log API access layer. Functions return the unwrapped `data` payload of
 * the `{ code, data, message }` envelope; errors surface as Axios errors so
 * callers can branch on `response.status` (e.g. 403 for employees).
 */

import { API_ROUTES } from "./api-routes";
import type { ApiEnvelope } from "./auth-types";
import { apiClient } from "./http-client";
import type {
  RunLogDetail,
  RunLogListParams,
  RunLogListResponse,
} from "./run-log-types";

export async function listRunLogs(
  params?: RunLogListParams,
): Promise<RunLogListResponse> {
  const response = await apiClient.get<ApiEnvelope<RunLogListResponse>>(
    API_ROUTES.runLogs,
    { params },
  );
  return response.data.data;
}

export async function getRunLog(traceId: string): Promise<RunLogDetail> {
  const response = await apiClient.get<ApiEnvelope<RunLogDetail>>(
    `${API_ROUTES.runLogs}/${traceId}`,
  );
  return response.data.data;
}
