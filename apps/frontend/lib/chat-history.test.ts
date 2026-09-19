import { describe, expect, it } from "vitest";

import { historyToChatMessages } from "./chat-history";
import type { HistoryMessage } from "./conversation-types";

function historyMessage(overrides: Partial<HistoryMessage> = {}): HistoryMessage {
  return {
    id: "m1",
    query: "hello",
    answer: "hi there",
    status: "normal",
    feedback: null,
    files: [],
    createdAt: "2026-09-19T00:00:00Z",
    ...overrides,
  };
}

describe("historyToChatMessages", () => {
  it("returns an empty transcript for an empty page", () => {
    expect(historyToChatMessages([])).toEqual([]);
  });

  it("expands one turn into a user bubble then an assistant bubble", () => {
    const messages = historyToChatMessages([historyMessage()]);
    expect(messages.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(messages.map((m) => m.content)).toEqual(["hello", "hi there"]);
  });

  it("reverses newest-first pages into oldest-first transcript order", () => {
    const messages = historyToChatMessages([
      historyMessage({ id: "m2", query: "latest", answer: "latest answer" }),
      historyMessage({ id: "m1", query: "first", answer: "first answer" }),
    ]);
    expect(messages.map((m) => m.content)).toEqual([
      "first",
      "first answer",
      "latest",
      "latest answer",
    ]);
  });

  it("drops empty query/answer halves instead of rendering blank bubbles", () => {
    const messages = historyToChatMessages([
      historyMessage({ id: "m1", query: "", answer: "welcome" }),
    ]);
    expect(messages).toHaveLength(1);
    expect(messages[0].role).toBe("assistant");
    expect(messages[0].content).toBe("welcome");
  });
});
