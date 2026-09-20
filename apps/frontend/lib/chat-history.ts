import type { ChatMessage, HistoryMessage } from "./conversation-types";

/**
 * Expand a newest-first history page into a chronological chat transcript.
 * Each turn (query + answer) becomes a user bubble then an assistant bubble;
 * empty halves are dropped so no blank bubbles render.
 */
export function historyToChatMessages(items: HistoryMessage[]): ChatMessage[] {
  const messages: ChatMessage[] = [];
  // The backend pages newest-first; walk oldest-first so turns stay ordered.
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.query) {
      messages.push({
        id: `${item.id}-user`,
        role: "user",
        content: item.query,
        createdAt: item.createdAt,
      });
    }
    if (item.answer) {
      messages.push({
        id: `${item.id}-assistant`,
        role: "assistant",
        content: item.answer,
        createdAt: item.createdAt,
      });
    }
  }
  return messages;
}
