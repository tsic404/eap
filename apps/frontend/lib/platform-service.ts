/**
 * Agent (and tool) API access layer.
 *
 * All functions return the unwrapped `data` payload of the backend's standard
 * `{ code, data, message }` envelope; errors are re-thrown as Axios errors so
 * callers can branch on `response.status` (e.g. 409 → optimistic-lock conflict).
 */

import axios from "axios";

import type {
  Agent,
  AgentDetail,
  AgentListParams,
  AgentListResponse,
  CreateAgentInput,
  Tool,
  UpdateAgentInput,
} from "./agent-types";
import type { ApiEnvelope } from "./auth-types";
import { API_ROUTES } from "./api-routes";
import { apiClient } from "./http-client";

export async function listAgents(
  params?: AgentListParams,
): Promise<AgentListResponse> {
  const response = await apiClient.get<ApiEnvelope<AgentListResponse>>(
    API_ROUTES.agents,
    { params },
  );
  return response.data.data;
}

/**
 * Fetch every matching agent across all pages. The backend caps `pageSize` at
 * 100, so a tenant with more than one page would otherwise be silently
 * truncated. The first page reveals `total`; the remaining pages are then
 * fetched in parallel.
 */
export async function listAllAgents(
  params?: Omit<AgentListParams, "page" | "pageSize">,
): Promise<Agent[]> {
  const pageSize = 100;
  const first = await listAgents({ ...params, page: 1, pageSize });
  const totalPages = Math.ceil(first.total / pageSize);
  if (totalPages <= 1) {
    return first.items;
  }

  const remaining = await Promise.all(
    Array.from({ length: totalPages - 1 }, (_, index) =>
      listAgents({ ...params, page: index + 2, pageSize }),
    ),
  );

  return [...first.items, ...remaining.flatMap((page) => page.items)];
}

export async function getAgent(agentId: string): Promise<AgentDetail> {
  const response = await apiClient.get<ApiEnvelope<AgentDetail>>(
    `${API_ROUTES.agents}/${agentId}`,
  );
  return response.data.data;
}

export async function createAgent(input: CreateAgentInput): Promise<Agent> {
  const response = await apiClient.post<ApiEnvelope<Agent>>(
    API_ROUTES.agents,
    input,
  );
  return response.data.data;
}

export async function updateAgent(
  agentId: string,
  input: UpdateAgentInput,
): Promise<Agent> {
  const response = await apiClient.patch<ApiEnvelope<Agent>>(
    `${API_ROUTES.agents}/${agentId}`,
    input,
  );
  return response.data.data;
}

export async function publishAgent(
  agentId: string,
  version: number,
): Promise<Agent> {
  const response = await apiClient.post<ApiEnvelope<Agent>>(
    `${API_ROUTES.agents}/${agentId}/publish`,
    { version },
  );
  return response.data.data;
}

export async function offlineAgent(agentId: string): Promise<Agent> {
  const response = await apiClient.post<ApiEnvelope<Agent>>(
    `${API_ROUTES.agents}/${agentId}/offline`,
  );
  return response.data.data;
}

export async function deleteAgent(agentId: string): Promise<void> {
  await apiClient.delete(`${API_ROUTES.agents}/${agentId}`);
}

export async function listTools(): Promise<Tool[]> {
  const response = await apiClient.get<ApiEnvelope<Tool[]>>(API_ROUTES.tools);
  return response.data.data;
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string };
}

/** Human-readable message from a failed request, with a safe fallback. */
export function extractApiErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const envelope = error.response?.data as ErrorEnvelope | undefined;
    if (envelope?.error?.message) {
      return envelope.error.message;
    }
    return error.message;
  }
  return error instanceof Error ? error.message : "请求失败，请稍后重试";
}

/** Whether an error is an optimistic-lock conflict (HTTP 409). */
export function isConflictError(error: unknown): boolean {
  return axios.isAxiosError(error) && error.response?.status === 409;
}
