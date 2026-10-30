"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { applySseEvent, initialChatStreamState } from "./chat-stream-state";
import type { ChatStreamState } from "./conversation-types";
import { postAgentDebug } from "./platform-service";
import { SseDecoder } from "./sse-decoder";
import { describeHttpError, describeStreamError, isAbortError } from "./stream-errors";

const HEARTBEAT_TIMEOUT_MS = 30_000;

/** Preparation failures the admin can act on, keyed by the backend error code. */
const DEBUG_FAILURE_MESSAGES: Record<string, string> = {
  AGENT_NOT_FOUND: "智能体不存在或已被删除",
  AGENT_API_KEY_MISSING: "该智能体缺少上游访问密钥，无法发起调试",
  VALIDATION_ERROR: "测试消息不能为空，且不超过 10000 字符",
  FORBIDDEN: "无权调试该智能体",
};

/** In-stream failures (C-101); the backend's message is not user-facing. */
const DEBUG_STREAM_ERROR_MESSAGES: Record<string, string> = {
  STREAM_ERROR: "回答生成中断，请重试",
};

/**
 * Runs one stateless debug turn at a time against an agent (C-127).
 *
 * Each send replaces the previous turn: the endpoint starts a fresh upstream
 * conversation and stores no transcript, so keeping older answers on screen
 * would suggest a context the next request never sends. The reply arrives as
 * the platform `message` deltas, and `message_end` carries the trace id of the
 * run log the turn wrote.
 */
export function useAgentDebug(agentId: string) {
  const [stream, setStream] = useState<ChatStreamState>(initialChatStreamState);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const heartbeatRef = useRef<number | null>(null);
  const abortedByUserRef = useRef(false);
  const streamingRef = useRef(false);
  const mountedRef = useRef(false);
  const messageSeqRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortedByUserRef.current = true;
      abortRef.current?.abort();
      if (heartbeatRef.current !== null) window.clearTimeout(heartbeatRef.current);
    };
  }, []);

  // A different agent is a different debug subject: drop the previous turn's
  // answer and trace id instead of showing them under the new agent's name.
  useEffect(() => {
    streamingRef.current = false;
    abortedByUserRef.current = true;
    abortRef.current?.abort();
    setStream(initialChatStreamState());
    setStreaming(false);
    setError(null);
  }, [agentId]);

  const send = useCallback(
    async (query: string): Promise<boolean> => {
      const trimmed = query.trim();
      if (!trimmed || streamingRef.current) return false;

      streamingRef.current = true;
      abortedByUserRef.current = false;
      setStreaming(true);
      setError(null);
      setStream({
        ...initialChatStreamState(),
        messages: [
          {
            id: `debug-user-${++messageSeqRef.current}`,
            role: "user",
            content: trimmed,
            createdAt: new Date().toISOString(),
          },
        ],
      });

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
          // No frame for 30s: the model stalled. Abort instead of waiting forever
          // (there is no resume; the admin sends the message again).
          controller.abort();
        }, HEARTBEAT_TIMEOUT_MS);
      };

      try {
        const response = await postAgentDebug(agentId, trimmed, controller.signal);
        if (!mountedRef.current) return false;

        const contentType = response.headers.get("content-type") ?? "";
        if (!response.ok || !contentType.includes("text/event-stream")) {
          setError(await describeHttpError(response, contentType, DEBUG_FAILURE_MESSAGES));
          return false;
        }
        if (!response.body) {
          setError("无法读取响应流");
          return false;
        }

        armHeartbeat();
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        const sse = new SseDecoder();
        let completed = false;
        let failed = false;

        while (true) {
          const { done, value } = await reader.read();
          if (!mountedRef.current) return false;
          if (done) break;
          const text = decoder.decode(value, { stream: true });
          for (const event of sse.push(text)) {
            armHeartbeat();
            if (event.event === "message_end") completed = true;
            if (event.event === "error") {
              failed = true;
              setError(describeStreamError(event.data, DEBUG_STREAM_ERROR_MESSAGES));
            }
            setStream((prev) => applySseEvent(prev, event));
          }
        }
        // A turn that ends without `message_end` was cut off mid-answer: without
        // the terminal frame there is no trace id either.
        if (!completed && !failed) {
          setError("连接中断，请重试");
          return false;
        }
        return true;
      } catch (err) {
        if (!mountedRef.current) return false;
        if (isAbortError(err)) {
          if (!abortedByUserRef.current) setError("连接超时，请重试");
          return false;
        }
        setError("连接中断，请重试");
        return false;
      } finally {
        clearHeartbeat();
        streamingRef.current = false;
        if (mountedRef.current) setStreaming(false);
      }
    },
    [agentId],
  );

  const abort = useCallback(() => {
    abortedByUserRef.current = true;
    abortRef.current?.abort();
    streamingRef.current = false;
    setStreaming(false);
  }, []);

  return {
    messages: stream.messages,
    traceId: stream.traceId,
    completed: stream.completed,
    streaming,
    error,
    send,
    abort,
  };
}
