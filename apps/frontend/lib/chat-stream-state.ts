/**
 * Pure reducer turning platform SSE events into chat stream state.
 *
 * Extracted from `useStreamChat` so the trickier rules — incremental text
 * append, messageId dedup, workflow node transitions — are deterministic and
 * unit-testable without a DOM or network.
 */

import type { ParsedSseEvent } from "./sse-decoder";
import type {
  ChatMessage,
  ChatStreamState,
  Citation,
  ToolCallRun,
  WorkflowNodeStatus,
  WorkflowState,
} from "./conversation-types";

let fallbackMessageCounter = 0;

function strOrNull(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

export function initialChatStreamState(): ChatStreamState {
  return {
    messages: [],
    citations: [],
    toolCalls: [],
    workflow: null,
    traceId: null,
    completed: false,
    truncationNotice: false,
    assistantId: null,
    renderedIds: [],
  };
}

export function applySseEvent(
  state: ChatStreamState,
  evt: ParsedSseEvent,
): ChatStreamState {
  switch (evt.event) {
    case "message":
      return appendAssistantText(state, strOrNull(evt.data.content), strOrNull(evt.data.messageId));
    case "agent_message":
      return appendAssistantText(state, strOrNull(evt.data.answer), strOrNull(evt.data.messageId));
    case "text_chunk":
      return appendAssistantText(state, strOrNull(evt.data.text), null);
    case "text_replace":
      return replaceAssistantText(state, strOrNull(evt.data.text));
    case "replace":
      return replaceAssistantText(state, strOrNull(evt.data.answer));
    case "message_end":
      return finishMessage(state, evt.data);
    case "agent_thought":
      return appendToolCall(state, evt.data);
    case "workflow_started":
      return {
        ...state,
        workflow: {
          workflowRunId: strOrNull(evt.data.workflowRunId) ?? "",
          status: "running",
          nodes: [],
        },
      };
    case "workflow_finished":
      return finishWorkflow(state, evt.data.status);
    case "node_started":
      return startNode(state, evt.data);
    case "node_finished":
      return finishNode(state, evt.data);
    // Consumed but not surfaced by any component in this module (parallel
    // branches, file links, audio): dropped intentionally.
    case "branch_started":
    case "branch_finished":
    case "file":
    case "tts_message":
    case "tts_message_end":
    case "error":
    case "ping":
      return state;
    default:
      return state;
  }
}

function appendAssistantText(
  state: ChatStreamState,
  text: string | null,
  messageId: string | null,
): ChatStreamState {
  if (!text) return state;

  // A replayed messageId after a reconnect points at an already-rendered
  // bubble that is not the one currently streaming — skip to avoid a duplicate.
  if (
    messageId !== null &&
    state.assistantId !== messageId &&
    state.renderedIds.includes(messageId)
  ) {
    return state;
  }

  const id = messageId ?? state.assistantId ?? `assistant-${++fallbackMessageCounter}`;
  const index = state.messages.findIndex(
    (message) => message.role === "assistant" && message.id === id,
  );

  if (index >= 0) {
    return {
      ...state,
      assistantId: id,
      messages: state.messages.map((message, i) =>
        i === index ? { ...message, content: message.content + text } : message,
      ),
    };
  }

  const message: ChatMessage = {
    id,
    role: "assistant",
    content: text,
    createdAt: new Date().toISOString(),
  };
  return {
    ...state,
    assistantId: id,
    renderedIds: [...state.renderedIds, id],
    messages: [...state.messages, message],
  };
}

function replaceAssistantText(state: ChatStreamState, text: string | null): ChatStreamState {
  if (text === null || state.assistantId === null) return state;
  return {
    ...state,
    messages: state.messages.map((message) =>
      message.role === "assistant" && message.id === state.assistantId
        ? { ...message, content: text }
        : message,
    ),
  };
}

function finishMessage(state: ChatStreamState, data: Record<string, unknown>): ChatStreamState {
  const metadata = data.metadata as
    | { retrieverResources?: unknown; truncationNotice?: unknown }
    | undefined;
  const raw = metadata?.retrieverResources;
  const citations: Citation[] = Array.isArray(raw) ? raw.map(toCitation) : [];
  return {
    ...state,
    traceId: strOrNull(data.traceId),
    citations,
    // Strict `=== true`: a missing/garbled flag degrades to `false`, never a
    // false banner on a healthy conversation.
    truncationNotice: metadata?.truncationNotice === true,
    completed: true,
  };
}

function toCitation(raw: unknown): Citation {
  const item = (raw ?? {}) as Record<string, unknown>;
  return {
    sourceName: strOrNull(item.sourceName) ?? "",
    kbName: strOrNull(item.kbName),
    excerpt: strOrNull(item.excerpt),
    score: typeof item.score === "number" ? item.score : null,
  };
}

function appendToolCall(state: ChatStreamState, data: Record<string, unknown>): ChatStreamState {
  const toolName = strOrNull(data.tool);
  // An `agent_thought` without a tool name is pure reasoning, not a tool call.
  if (!toolName) return state;

  const observation = strOrNull(data.observation);
  const id = strOrNull(data.thoughtId) ?? `tool-${toolName}-${state.toolCalls.length}`;
  const existing = state.toolCalls.find((call) => call.id === id);

  if (existing) {
    return {
      ...state,
      toolCalls: state.toolCalls.map((call) =>
        call.id === id
          ? {
              ...call,
              status: observation ? "success" : "running",
              requestSummary: strOrNull(data.thought) ?? call.requestSummary,
              responseSummary: observation ?? call.responseSummary,
            }
          : call,
      ),
    };
  }

  const call: ToolCallRun = {
    id,
    toolName,
    status: observation ? "success" : "running",
    requestSummary: strOrNull(data.thought),
    responseSummary: observation,
  };
  return { ...state, toolCalls: [...state.toolCalls, call] };
}

function finishWorkflow(state: ChatStreamState, status: unknown): ChatStreamState {
  if (!state.workflow) return state;
  return { ...state, workflow: { ...state.workflow, status: toWorkflowStatus(status) } };
}

function startNode(state: ChatStreamState, data: Record<string, unknown>): ChatStreamState {
  if (!state.workflow) return state;
  const nodeId = strOrNull(data.nodeId) ?? `node-${state.workflow.nodes.length}`;
  const node = { id: nodeId, title: strOrNull(data.title), status: "running" as const };
  return {
    ...state,
    workflow: { ...state.workflow, nodes: [...state.workflow.nodes, node] },
  };
}

function finishNode(state: ChatStreamState, data: Record<string, unknown>): ChatStreamState {
  if (!state.workflow) return state;
  const nodeId = strOrNull(data.nodeId);
  if (!nodeId) return state;
  return {
    ...state,
    workflow: {
      ...state.workflow,
      nodes: state.workflow.nodes.map((node) =>
        node.id === nodeId ? { ...node, status: toNodeStatus(data.status) } : node,
      ),
    },
  };
}

function toWorkflowStatus(status: unknown): WorkflowState["status"] {
  const value = strOrNull(status);
  if (value === "failed") return "failed";
  if (value === "running") return "running";
  return "succeeded";
}

function toNodeStatus(status: unknown): WorkflowNodeStatus {
  const value = strOrNull(status);
  if (value === "failed") return "failed";
  if (value === "running") return "running";
  if (value === "stopped" || value === "blocked") return "stopped";
  return "success";
}
