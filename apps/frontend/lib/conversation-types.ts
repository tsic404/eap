/**
 * Conversation/chat domain types, mirroring the backend wire contract
 * (`app/schemas/conversation.py` and the platform SSE event envelope produced
 * by `DifyConversationAdapter`). Field names are camelCase to match the API.
 */

export type MessageRole = "user" | "assistant";

/** Item returned by `GET /api/conversations` and `GET /api/conversations/{id}`. */
export interface Conversation {
  id: string;
  title: string | null;
  agentId: string;
  agentName: string | null;
  status: string;
  createdAt: string;
  updatedAt: string;
}

/** Cursor-paginated envelope returned by `GET /api/conversations`. */
export interface ConversationPage {
  items: Conversation[];
  nextCursor: string | null;
}

/** A single chat bubble. Assistant messages are keyed by the SSE `messageId`. */
export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  createdAt: string;
}

/** A knowledge citation surfaced by `message_end` (retriever resources). */
export interface Citation {
  sourceName: string;
  kbName?: string | null;
  excerpt?: string | null;
  score?: number | null;
}

export type RunStatus = "success" | "failed" | "running" | "blocked";

/** A tool-call step derived from `agent_thought` events that name a tool. */
export interface ToolCallRun {
  id: string;
  toolName: string;
  status: RunStatus;
  requestSummary?: string | null;
  responseSummary?: string | null;
}

export type WorkflowNodeStatus = "running" | "success" | "failed" | "stopped";

export interface WorkflowNode {
  id: string;
  title: string | null;
  status: WorkflowNodeStatus;
}

export interface WorkflowState {
  workflowRunId: string;
  status: "running" | "succeeded" | "failed";
  nodes: WorkflowNode[];
}

/** Pure, per-turn streaming state consumed by `useStreamChat`. */
export interface ChatStreamState {
  messages: ChatMessage[];
  citations: Citation[];
  toolCalls: ToolCallRun[];
  workflow: WorkflowState | null;
  traceId: string | null;
  /** `true` once `message_end` has been received for the active turn. */
  completed: boolean;
  /** `true` when the backend reports token truncation on `message_end` metadata. */
  truncationNotice: boolean;
  /** Message id of the assistant bubble currently being streamed into. */
  assistantId: string | null;
  /** Every assistant message id already rendered, used to dedup replays. */
  renderedIds: string[];
}

/** Structured rate-limit error decoded from a non-SSE (JSON) 429 response. */
export interface RateLimitInfo {
  code: string | null;
  message: string | null;
}
