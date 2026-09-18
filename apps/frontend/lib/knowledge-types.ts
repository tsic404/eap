/**
 * Knowledge-base domain types mirroring the backend `app/schemas/knowledge.py`
 * DTOs. Unlike the agent DTOs (which the backend maps to camelCase), these are
 * serialized from Pydantic models directly, so the keys stay snake_case.
 */

export const DOCUMENT_STATUSES = ["indexing", "completed", "failed"] as const;
export type DocumentStatus = (typeof DOCUMENT_STATUSES)[number];

/** List item returned by `GET /api/knowledge-bases` and `GET /api/knowledge-bases/{id}`. */
export interface KnowledgeBase {
  kb_id: string;
  name: string;
  description: string | null;
  type: string;
  indexing_status: string | null;
  doc_count: number;
  chunk_count: number;
  created_at: string;
}

/** Paginated envelope returned by `GET /api/knowledge-bases`. */
export interface KnowledgeBaseListResponse {
  items: KnowledgeBase[];
  total: number;
}

/** Body of `POST /api/knowledge-bases`. */
export interface CreateKnowledgeBaseInput {
  name: string;
  description?: string | null;
  type?: string;
}

/** Document returned by upload/list endpoints (`status` = indexing/completed/failed). */
export interface KnowledgeDocument {
  id: string;
  name: string;
  status: DocumentStatus;
}

/** Paginated document listing. */
export interface DocumentListResponse {
  items: KnowledgeDocument[];
  total: number;
}

/** Indexing status of a single document (polled from Dify). */
export interface DocumentStatusInfo {
  id: string;
  status: DocumentStatus;
  error: string | null;
}

/** Body of `POST /api/knowledge-bases/{id}/retrieval-test`. */
export interface RetrieveTestInput {
  query: string;
  retrieval_model?: { top_k: number };
}

/** A single recalled chunk from the retrieve response. */
export interface RetrievalCitation {
  content: string;
  score: number;
  source_name: string | null;
}

/** Retrieval test result: citations plus the top score and round-trip latency. */
export interface RetrievalTestResult {
  query: string;
  citations: RetrievalCitation[];
  score: number;
  latencyMs: number;
}
