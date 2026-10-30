/**
 * Agent (and tool) API access layer.
 *
 * All functions return the unwrapped `data` payload of the backend's standard
 * `{ code, data, message }` envelope; errors are re-thrown as Axios errors so
 * callers can branch on `response.status` plus `error.code` — several distinct
 * failures share HTTP 409.
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
import { extractApiErrorCode } from "./api-error";
import { API_BASE_URL, API_ROUTES } from "./api-routes";
import { apiClient } from "./http-client";
import { refreshAccessToken } from "./refresh-channel";
import { getAccessToken } from "./token-store";

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

export async function startTestingAgent(
  agentId: string,
  version: number,
): Promise<Agent> {
  const response = await apiClient.post<ApiEnvelope<Agent>>(
    `${API_ROUTES.agents}/${agentId}/start-testing`,
    { version },
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

export async function offlineAgent(agentId: string, version: number): Promise<Agent> {
  const response = await apiClient.post<ApiEnvelope<Agent>>(
    `${API_ROUTES.agents}/${agentId}/offline`,
    { version },
  );
  return response.data.data;
}

export async function deleteAgent(agentId: string): Promise<void> {
  await apiClient.delete(`${API_ROUTES.agents}/${agentId}`);
}

/**
 * POST one debug message and return the raw SSE `Response` (C-127). Raw `fetch`
 * because axios cannot expose a `ReadableStream`; the caller owns the stream
 * lifecycle and the JSON-error branch of a failed preparation.
 *
 * The access token lives in memory for minutes only, so a turn sent after it
 * expires must renew and replay like the axios client does (C-2) — otherwise
 * resending from the panel keeps replaying the dead token.
 */
export async function postAgentDebug(
  agentId: string,
  query: string,
  signal: AbortSignal,
): Promise<Response> {
  const url = `${API_BASE_URL}${API_ROUTES.agents}/${agentId}/debug`;
  const send = (token: string | null): Promise<Response> =>
    fetch(url, {
      method: "POST",
      signal,
      headers: token
        ? { "Content-Type": "application/json", Authorization: `Bearer ${token}` }
        : { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });

  const response = await send(getAccessToken());
  if (response.status !== 401) return response;

  const { token: refreshed } = await refreshAccessToken();
  if (refreshed === null) return response;

  // The rejected body is never read; release it before the replay.
  await response.body?.cancel();
  return send(refreshed);
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

function isApiError(error: unknown, status: number, code: string): boolean {
  return (
    axios.isAxiosError(error) &&
    error.response?.status === status &&
    extractApiErrorCode(error) === code
  );
}

/** Whether an error is an optimistic-lock version conflict (HTTP 409 `CONFLICT`). */
export function isConflictError(error: unknown): boolean {
  return isApiError(error, 409, "CONFLICT");
}

/** Whether an action was rejected because the resource state forbids it (409 `INVALID_STATE`). */
export function isInvalidStateError(error: unknown): boolean {
  return isApiError(error, 409, "INVALID_STATE");
}

/** Whether an agent id already exists (HTTP 409 `DUPLICATE_AGENT_ID`). */
export function isDuplicateAgentError(error: unknown): boolean {
  return isApiError(error, 409, "DUPLICATE_AGENT_ID");
}
