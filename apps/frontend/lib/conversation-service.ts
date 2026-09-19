/**
 * Conversation API access layer. REST calls return the unwrapped `data` payload
 * of the `{ code, data, message }` envelope (errors surface as Axios errors so
 * callers branch on `response.status`). The streaming call uses raw `fetch`
 * because axios cannot expose a `ReadableStream` for SSE consumption.
 */

import { API_ROUTES } from "./api-routes";
import type { ApiEnvelope } from "./auth-types";
import type {
  Conversation,
  ConversationPage,
  MessageFile,
  UploadedFile,
} from "./conversation-types";
import { apiClient, API_BASE_URL } from "./http-client";
import { getAccessToken } from "./token-store";

export interface SendMessageInput {
  query: string;
  agentId: string;
  files?: MessageFile[];
}

export async function listConversations(
  limit = 20,
  cursor?: string,
): Promise<ConversationPage> {
  const response = await apiClient.get<ApiEnvelope<ConversationPage>>(
    API_ROUTES.conversations,
    { params: { limit, ...(cursor ? { cursor } : {}) } },
  );
  return response.data.data;
}

export async function getConversation(conversationId: string): Promise<Conversation> {
  const response = await apiClient.get<ApiEnvelope<Conversation>>(
    `${API_ROUTES.conversations}/${conversationId}`,
  );
  return response.data.data;
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await apiClient.delete(`${API_ROUTES.conversations}/${conversationId}`);
}

/**
 * POST the message and return the raw SSE `Response`. Callers own the stream
 * lifecycle (reading/aborting) and the JSON-error branch for non-SSE bodies.
 */
export async function postMessage(
  conversationId: string,
  input: SendMessageInput,
  signal: AbortSignal,
): Promise<Response> {
  const token = getAccessToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  return fetch(
    `${API_BASE_URL}${API_ROUTES.conversations}/${conversationId}/messages`,
    {
      method: "POST",
      signal,
      headers,
      body: JSON.stringify({
        query: input.query,
        agentId: input.agentId,
        files: input.files,
      }),
    },
  );
}

/**
 * Upload a chat attachment, returning its Dify upload-file id (not a knowledge
 * document id). The id is echoed back as ``MessageFile.id`` on the next send.
 */
export async function uploadChatFile(file: File): Promise<UploadedFile> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await apiClient.post<ApiEnvelope<UploadedFile>>(
    `${API_ROUTES.files}/upload`,
    formData,
    {
      // FormData needs the browser to set the multipart boundary; drop the
      // instance default `application/json` header so axios can do that.
      headers: { "Content-Type": undefined },
    },
  );
  return response.data.data;
}
