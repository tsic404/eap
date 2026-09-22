"use client";

import useSWR from "swr";

import { getConversation, listConversations } from "./conversation-service";

/** SWR-backed conversation list (first page). */
export function useConversations() {
  // A first paint can lose the race with the session bootstrap or with a
  // dependency that is still restarting. SWR's default 5s backoff leaves the
  // error banner on screen long after the cause is gone, so retry promptly —
  // bounded, so a persistent outage stops after three attempts instead of
  // hammering the backend for as long as the page is open.
  return useSWR("conversations", () => listConversations(), {
    errorRetryInterval: 500,
    errorRetryCount: 3,
  });
}

/** SWR-backed single conversation (for the detail header: agent name, title). */
export function useConversation(conversationId: string | undefined) {
  return useSWR(
    conversationId ? `conversation:${conversationId}` : null,
    () => getConversation(conversationId!),
  );
}
