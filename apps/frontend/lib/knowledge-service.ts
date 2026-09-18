/**
 * Knowledge-base API access layer. All functions return the unwrapped `data`
 * payload of the `{ code, data, message }` envelope; errors surface as Axios
 * errors so callers can branch on `response.status`.
 */

import type { AxiosProgressEvent } from "axios";

import { API_ROUTES } from "./api-routes";
import type { ApiEnvelope } from "./auth-types";
import { apiClient } from "./http-client";
import type {
  CreateKnowledgeBaseInput,
  DocumentListResponse,
  DocumentStatusInfo,
  KnowledgeBase,
  KnowledgeBaseListResponse,
  KnowledgeDocument,
  RetrieveTestInput,
  RetrievalTestResult,
} from "./knowledge-types";
import { getAgent, listAllAgents } from "./platform-service";

export async function listKnowledgeBases(
  page = 1,
  limit = 20,
): Promise<KnowledgeBaseListResponse> {
  const response = await apiClient.get<ApiEnvelope<KnowledgeBaseListResponse>>(
    API_ROUTES.knowledgeBases,
    { params: { page, limit } },
  );
  return response.data.data;
}

export async function createKnowledgeBase(
  input: CreateKnowledgeBaseInput,
): Promise<KnowledgeBase> {
  const response = await apiClient.post<ApiEnvelope<KnowledgeBase>>(
    API_ROUTES.knowledgeBases,
    input,
  );
  return response.data.data;
}

export async function getKnowledgeBase(kbId: string): Promise<KnowledgeBase> {
  const response = await apiClient.get<ApiEnvelope<KnowledgeBase>>(
    `${API_ROUTES.knowledgeBases}/${kbId}`,
  );
  return response.data.data;
}

export async function deleteKnowledgeBase(kbId: string): Promise<void> {
  await apiClient.delete(`${API_ROUTES.knowledgeBases}/${kbId}`);
}

export async function uploadDocument(
  kbId: string,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<KnowledgeDocument> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await apiClient.post<ApiEnvelope<KnowledgeDocument>>(
    `${API_ROUTES.knowledgeBases}/${kbId}/documents`,
    formData,
    {
      // FormData needs the browser to set the multipart boundary; drop the
      // instance default `application/json` header so axios can do that.
      headers: { "Content-Type": undefined },
      onUploadProgress: (event: AxiosProgressEvent) => {
        if (event.total && onProgress) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      },
    },
  );
  return response.data.data;
}

export async function listDocuments(
  kbId: string,
  page = 1,
  limit = 20,
): Promise<DocumentListResponse> {
  const response = await apiClient.get<ApiEnvelope<DocumentListResponse>>(
    `${API_ROUTES.knowledgeBases}/${kbId}/documents`,
    { params: { page, limit } },
  );
  return response.data.data;
}

export async function getDocumentStatus(
  kbId: string,
  documentId: string,
): Promise<DocumentStatusInfo> {
  const response = await apiClient.get<ApiEnvelope<DocumentStatusInfo>>(
    `${API_ROUTES.knowledgeBases}/${kbId}/documents/${documentId}/status`,
  );
  return response.data.data;
}

export async function retrievalTest(
  kbId: string,
  input: RetrieveTestInput,
): Promise<RetrievalTestResult> {
  const response = await apiClient.post<ApiEnvelope<RetrievalTestResult>>(
    `${API_ROUTES.knowledgeBases}/${kbId}/retrieval-test`,
    input,
  );
  return response.data.data;
}

/**
 * Names of the agents bound to a knowledge base, for the delete confirmation.
 *
 * The agent list endpoint omits bindings and there is no binding-filter query,
 * so this resolves each agent's detail to find the ones that reference `kbId`.
 * That is N+1 requests bounded by the tenant's agent count — acceptable for an
 * on-demand admin confirmation, but worth collapsing into one backend query if
 * agent counts grow large.
 */
export async function listAgentsBoundToKnowledge(
  kbId: string,
): Promise<string[]> {
  const agents = await listAllAgents();
  const details = await Promise.all(
    agents.map((agent) => getAgent(agent.agentId)),
  );
  return details
    .filter((detail) =>
      detail.boundKnowledge.some((binding) => binding.kbId === kbId),
    )
    .map((detail) => detail.name);
}
