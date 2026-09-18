/**
 * Tool API access layer. Functions return the unwrapped `data` payload of the
 * `{ code, data, message }` envelope; errors are re-thrown as Axios errors.
 */

import { API_ROUTES } from "./api-routes";
import type { ApiEnvelope } from "./auth-types";
import { apiClient } from "./http-client";
import type {
  CreateToolInput,
  DebugToolInput,
  DebugToolResult,
  ToolDetail,
  UpdateToolInput,
} from "./tool-types";

export async function listTools(): Promise<ToolDetail[]> {
  const response = await apiClient.get<ApiEnvelope<ToolDetail[]>>(
    API_ROUTES.tools,
  );
  return response.data.data;
}

export async function getTool(toolId: string): Promise<ToolDetail> {
  const response = await apiClient.get<ApiEnvelope<ToolDetail>>(
    `${API_ROUTES.tools}/${toolId}`,
  );
  return response.data.data;
}

export async function createTool(input: CreateToolInput): Promise<ToolDetail> {
  const response = await apiClient.post<ApiEnvelope<ToolDetail>>(
    API_ROUTES.tools,
    input,
  );
  return response.data.data;
}

export async function updateTool(
  toolId: string,
  input: UpdateToolInput,
): Promise<ToolDetail> {
  const response = await apiClient.patch<ApiEnvelope<ToolDetail>>(
    `${API_ROUTES.tools}/${toolId}`,
    input,
  );
  return response.data.data;
}

export async function deleteTool(toolId: string): Promise<void> {
  await apiClient.delete(`${API_ROUTES.tools}/${toolId}`);
}

export async function debugTool(
  toolId: string,
  input: DebugToolInput,
): Promise<DebugToolResult> {
  const response = await apiClient.post<ApiEnvelope<DebugToolResult>>(
    `${API_ROUTES.tools}/${toolId}/debug`,
    input,
  );
  return response.data.data;
}
