import { describe, expect, it } from "vitest";

import { applySseEvent, initialChatStreamState } from "./chat-stream-state";
import type { ParsedSseEvent } from "./sse-decoder";

function event(name: string, data: Record<string, unknown>): ParsedSseEvent {
  return { event: name, data };
}

describe("applySseEvent", () => {
  it("appends incremental chunks into one assistant bubble", () => {
    let state = initialChatStreamState();
    state = applySseEvent(state, event("message", { content: "你", messageId: "m1" }));
    state = applySseEvent(state, event("message", { content: "好", messageId: "m1" }));
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].role).toBe("assistant");
    expect(state.messages[0].content).toBe("你好");
  });

  it("does not duplicate a replayed messageId after reconnect", () => {
    let state = initialChatStreamState();
    state = applySseEvent(state, event("message", { content: "你", messageId: "m1" }));
    state = applySseEvent(state, event("message_end", { traceId: "t1", metadata: {} }));
    // Reconnect resets the streaming pointer (mirrors useStreamChat).
    state = { ...state, assistantId: null };
    state = applySseEvent(state, event("message", { content: "你", messageId: "m1" }));
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].content).toBe("你");
  });

  it("creates a second bubble for a new messageId", () => {
    let state = initialChatStreamState();
    state = applySseEvent(state, event("message", { content: "一", messageId: "m1" }));
    state = applySseEvent(state, event("message", { content: "二", messageId: "m2" }));
    expect(state.messages.map((message) => message.content)).toEqual(["一", "二"]);
  });

  it("appends text_chunk into the current assistant bubble", () => {
    let state = initialChatStreamState();
    state = applySseEvent(state, event("text_chunk", { text: "流" }));
    state = applySseEvent(state, event("text_chunk", { text: "式" }));
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].content).toBe("流式");
  });

  it("collects citations and traceId on message_end", () => {
    let state = initialChatStreamState();
    state = applySseEvent(
      state,
      event("message_end", {
        traceId: "trace1",
        metadata: {
          retrieverResources: [
            { sourceName: "doc1", kbName: "kb1", excerpt: "ex", score: 0.9 },
          ],
        },
      }),
    );
    expect(state.completed).toBe(true);
    expect(state.traceId).toBe("trace1");
    expect(state.citations).toEqual([
      { sourceName: "doc1", kbName: "kb1", excerpt: "ex", score: 0.9 },
    ]);
  });

  it("tracks workflow node progress", () => {
    let state = initialChatStreamState();
    state = applySseEvent(state, event("workflow_started", { workflowRunId: "wf1" }));
    state = applySseEvent(state, event("node_started", { nodeId: "n1", title: "检索" }));
    state = applySseEvent(state, event("node_finished", { nodeId: "n1", status: "succeeded" }));
    expect(state.workflow?.nodes).toHaveLength(1);
    expect(state.workflow?.nodes[0].status).toBe("success");
    expect(state.workflow?.status).toBe("running");
  });

  it("marks the workflow finished on workflow_finished", () => {
    let state = initialChatStreamState();
    state = applySseEvent(state, event("workflow_started", { workflowRunId: "wf1" }));
    state = applySseEvent(state, event("workflow_finished", { status: "succeeded" }));
    expect(state.workflow?.status).toBe("succeeded");
  });

  it("derives tool-call steps from agent_thought", () => {
    let state = initialChatStreamState();
    state = applySseEvent(
      state,
      event("agent_thought", { thoughtId: "th1", thought: "calling", tool: "search" }),
    );
    state = applySseEvent(
      state,
      event("agent_thought", { thoughtId: "th1", tool: "search", observation: "found" }),
    );
    expect(state.toolCalls).toHaveLength(1);
    expect(state.toolCalls[0].toolName).toBe("search");
    expect(state.toolCalls[0].status).toBe("success");
  });

  it("replaces the assistant text on text_replace", () => {
    let state = initialChatStreamState();
    state = applySseEvent(state, event("message", { content: "你好", messageId: "m1" }));
    state = applySseEvent(state, event("text_replace", { text: "替换" }));
    expect(state.messages[0].content).toBe("替换");
  });
});
