"use client";

import useSWR from "swr";

import { getConversation, listConversations } from "./conversation-service";

/** SWR-backed conversation list (first page). */
export function useConversations() {
  return useSWR("conversations", () => listConversations());
}

/** SWR-backed single conversation (for the detail header: agent name, title). */
export function useConversation(conversationId: string | undefined) {
  return useSWR(
    conversationId ? `conversation:${conversationId}` : null,
    () => getConversation(conversationId!),
  );
}
