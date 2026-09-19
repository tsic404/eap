"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { historyToChatMessages } from "./chat-history";
import { applySseEvent, initialChatStreamState } from "./chat-stream-state";
import type { ChatStreamState, HistoryMessage, RateLimitInfo } from "./conversation-types";
import { listMessages, postMessage } from "./conversation-service";
import { SseDecoder } from "./sse-decoder";
import { toast } from "./toast-bus";

const HEARTBEAT_TIMEOUT_MS = 30_000;
const RATE_LIMIT_COOLDOWN_SECONDS = 60;
/** History messages fetched per page (the backend caps `limit` at 100). */
const HISTORY_PAGE_SIZE = 100;

type StreamOutcome = "completed" | "aborted" | "timeout" | "disconnected" | "terminal";

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string };
}

async function decodeErrorEnvelope(response: Response): Promise<RateLimitInfo> {
  const body = (await response.json().catch(() => null)) as ErrorEnvelope | null;
  return { code: body?.error?.code ?? null, message: body?.error?.message ?? null };
}

async function describeHttpError(response: Response, contentType: string): Promise<string> {
  if (contentType.includes("application/json")) {
    const info = await decodeErrorEnvelope(response);
    if (info.message) return info.message;
  }
  return `请求失败（HTTP ${response.status}）`;
}

function describeStreamError(data: Record<string, unknown>): string {
  const message = typeof data.message === "string" ? data.message : null;
  const code = typeof data.code === "string" ? data.code : null;
  return message ?? code ?? "对话生成出错";
}

/** Follow the history cursor to its end, accumulating newest-first items. */
async function fetchMessageHistory(conversationId: string): Promise<HistoryMessage[]> {
  const items: HistoryMessage[] = [];
  let cursor: string | undefined;
  // Loop until Dify reports no older messages; guard against an empty page or
  // a non-advancing cursor so a misbehaving upstream cannot spin forever.
  while (true) {
    const page = await listMessages(conversationId, HISTORY_PAGE_SIZE, cursor);
    items.push(...page.items);
    if (!page.nextCursor || page.items.length === 0 || page.nextCursor === cursor) break;
    cursor = page.nextCursor;
  }
  return items;
}

/**
 * Consumes the conversation's SSE stream for a single chat turn.
 *
 * There is no automatic reconnect: re-POSTing `/messages` would start a second
 * Dify turn (the backend has no resume/idempotency key), duplicating the reply
 * and any tool side effects. A 30s heartbeat aborts a stalled stream and an
 * interrupted stream surfaces a retryable error; the user re-sends explicitly.
 * `conversationId`/`agentId` are null until the conversation has loaded.
 */
export function useStreamChat(conversationId: string | null, agentId: string | null) {
  const [stream, setStream] = useState<ChatStreamState>(initialChatStreamState);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rateLimitSeconds, setRateLimitSeconds] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyReloadKey, setHistoryReloadKey] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const heartbeatRef = useRef<number | null>(null);
  const abortedByUserRef = useRef(false);
  const streamingRef = useRef(false);
  const mountedRef = useRef(false);
  const lastQueryRef = useRef<string | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortedByUserRef.current = true;
      abortRef.current?.abort();
      if (heartbeatRef.current !== null) window.clearTimeout(heartbeatRef.current);
    };
  }, []);

  // Reset per-conversation state when the conversation changes so a previous
  // conversation's transcript, stream, and errors cannot leak into the next.
  useEffect(() => {
    setStream(initialChatStreamState());
    setStreaming(false);
    setError(null);
    streamingRef.current = false;
    abortedByUserRef.current = true;
    lastQueryRef.current = null;
    abortRef.current?.abort();
  }, [conversationId]);

  // Load message history once the conversation is known, following the cursor
  // to the end so reopening a conversation restores the full transcript.
  useEffect(() => {
    if (!conversationId) {
      setHistoryLoading(false);
      return;
    }
    let cancelled = false;
    setHistoryLoading(true);
    setHistoryError(null);
    void (async () => {
      try {
        const items = await fetchMessageHistory(conversationId);
        if (!cancelled) {
          // Preserve any message sent this session (possible only after a
          // failed first load — the composer is disabled while it loads).
          setStream((prev) => ({
            ...prev,
            messages: [...historyToChatMessages(items), ...prev.messages],
          }));
        }
      } catch {
        if (!cancelled) setHistoryError("历史消息加载失败，请重试");
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId, historyReloadKey]);

  // Rate-limit countdown: tick once per second while a cooldown is active.
  useEffect(() => {
    if (rateLimitSeconds <= 0) return;
    const timer = window.setTimeout(() => {
      setRateLimitSeconds((seconds) => Math.max(0, seconds - 1));
    }, 1_000);
    return () => window.clearTimeout(timer);
  }, [rateLimitSeconds]);

  const streamOnce = useCallback(
    async (query: string): Promise<StreamOutcome> => {
      if (!conversationId || !agentId) return "terminal";

      const controller = new AbortController();
      abortRef.current = controller;

      const clearHeartbeat = () => {
        if (heartbeatRef.current !== null) {
          window.clearTimeout(heartbeatRef.current);
          heartbeatRef.current = null;
        }
      };
      const armHeartbeat = () => {
        clearHeartbeat();
        heartbeatRef.current = window.setTimeout(() => {
          // No event for 30s → abort the stalled stream (no auto-reconnect).
          controller.abort();
        }, HEARTBEAT_TIMEOUT_MS);
      };

      let completed = false;

      try {
        const response = await postMessage(
          conversationId,
          { query, agentId },
          controller.signal,
        );
        if (!mountedRef.current) return "aborted";

        const contentType = response.headers.get("content-type") ?? "";

        if (response.status === 429) {
          const info = await decodeErrorEnvelope(response);
          if (!mountedRef.current) return "aborted";
          if (info.code === "QUOTA_EXCEEDED") {
            toast({ type: "warning", title: "本月 API 调用量已用完，请联系管理员升级" });
          } else {
            toast({ type: "warning", title: "消息发送太频繁，请稍后再试（每分钟最多 20 条）" });
            setRateLimitSeconds(RATE_LIMIT_COOLDOWN_SECONDS);
          }
          return "terminal";
        }

        if (!response.ok || !contentType.includes("text/event-stream")) {
          const message = await describeHttpError(response, contentType);
          if (!mountedRef.current) return "aborted";
          setError(message);
          return "terminal";
        }
        if (!response.body) {
          setError("无法读取响应流");
          return "terminal";
        }

        armHeartbeat();
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        const sse = new SseDecoder();

        while (true) {
          const { done, value } = await reader.read();
          if (!mountedRef.current) return "aborted";
          if (done) break;
          const text = decoder.decode(value, { stream: true });
          for (const event of sse.push(text)) {
            armHeartbeat();
            if (event.event === "message_end") completed = true;
            if (event.event === "error") setError(describeStreamError(event.data));
            setStream((prev) => applySseEvent(prev, event));
          }
        }

        return completed ? "completed" : "disconnected";
      } catch (err) {
        if (isAbortError(err)) {
          return abortedByUserRef.current ? "aborted" : "timeout";
        }
        return "disconnected";
      } finally {
        clearHeartbeat();
      }
    },
    [conversationId, agentId],
  );

  const sendMessage = useCallback(
    async (query: string) => {
      const trimmed = query.trim();
      if (!trimmed || !conversationId || !agentId || streamingRef.current) return;

      streamingRef.current = true;
      setStreaming(true);
      setError(null);
      setRateLimitSeconds(0);
      abortedByUserRef.current = false;
      lastQueryRef.current = trimmed;

      setStream((prev) => ({
        messages: [
          ...prev.messages,
          {
            id: `user-${Date.now()}`,
            role: "user",
            content: trimmed,
            createdAt: new Date().toISOString(),
          },
        ],
        citations: [],
        toolCalls: [],
        workflow: null,
        traceId: null,
        completed: false,
        assistantId: null,
        renderedIds: prev.renderedIds,
      }));

      const outcome = await streamOnce(trimmed);
      if (!mountedRef.current) return;

      streamingRef.current = false;
      setStreaming(false);

      if (outcome === "completed" || outcome === "aborted") {
        setError(null);
      } else if (outcome === "timeout") {
        setError("连接超时，请重试");
      } else if (outcome === "disconnected") {
        setError("连接中断，请重试");
      }
      // "terminal": the 429 toast / HTTP error banner already surfaced the cause.
    },
    [conversationId, agentId, streamOnce],
  );

  const abort = useCallback(() => {
    abortedByUserRef.current = true;
    abortRef.current?.abort();
    streamingRef.current = false;
    setStreaming(false);
    setError(null);
  }, []);

  const retry = useCallback(() => {
    if (lastQueryRef.current) void sendMessage(lastQueryRef.current);
  }, [sendMessage]);

  const dismissError = useCallback(() => setError(null), []);

  const retryHistory = useCallback(() => {
    setHistoryReloadKey((key) => key + 1);
  }, []);

  return {
    messages: stream.messages,
    citations: stream.citations,
    toolCalls: stream.toolCalls,
    workflow: stream.workflow,
    traceId: stream.traceId,
    streaming,
    error,
    rateLimitSeconds,
    historyLoading,
    historyError,
    sendMessage,
    retry,
    retryHistory,
    abort,
    dismissError,
  };
}
