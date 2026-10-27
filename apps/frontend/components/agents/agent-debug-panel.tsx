"use client";

import { useEffect, useState } from "react";
import { Send, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useAgentDebug } from "@/lib/use-agent-debug";
import type { AgentDetail } from "@/lib/agent-types";

import { StreamedAnswer } from "./streamed-answer";

const COPY_FEEDBACK_MS = 2_000;

const COPY_LABEL = {
  idle: "复制",
  copied: "已复制",
  failed: "复制失败",
} as const;

type CopyState = keyof typeof COPY_LABEL;

export interface AgentDebugPanelProps {
  agent: AgentDetail;
}

/**
 * Debug tab: one stateless test turn against the agent's real upstream model.
 *
 * The user bubble is appended before the request leaves, the answer is revealed
 * as its `message` deltas arrive, and the terminal `message_end` supplies the
 * trace id the run log was written under (C-127) — copyable so an admin can
 * look the turn up in the log pages.
 */
export function AgentDebugPanel({ agent }: AgentDebugPanelProps) {
  const { messages, traceId, streaming, error, send, abort } = useAgentDebug(agent.agentId);
  const [value, setValue] = useState("");
  const [copyState, setCopyState] = useState<CopyState>("idle");

  useEffect(() => {
    if (copyState === "idle") return;
    const timer = window.setTimeout(() => setCopyState("idle"), COPY_FEEDBACK_MS);
    return () => window.clearTimeout(timer);
  }, [copyState]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || streaming) return;
    // The message lives in the transcript from here on; keeping it in the
    // composer would leave the same text on screen twice until the answer ends.
    setValue("");
    void send(trimmed);
  };

  const copyTrace = async () => {
    if (!traceId) return;
    try {
      await navigator.clipboard.writeText(traceId);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted-foreground">
        每次发送都是一次全新的调试回合，不延续上一回合上下文。
      </p>

      <div className="flex min-h-56 flex-col gap-3 rounded-lg border border-border bg-muted p-4">
        {messages.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            发送一条测试消息，查看智能体对真实模型调用的回答。
          </p>
        ) : (
          messages.map((message) =>
            message.role === "user" ? (
              <div key={message.id} className="flex justify-end">
                <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-primary px-4 py-3 text-sm text-primary-foreground">
                  {message.content}
                </p>
              </div>
            ) : (
              <div key={message.id} className="flex justify-start">
                <div className="max-w-[85%] rounded-lg border border-border bg-background px-4 py-3">
                  <StreamedAnswer text={message.content} streaming={streaming} />
                </div>
              </div>
            ),
          )
        )}
        {streaming && !messages.some((message) => message.role === "assistant") && (
          <p className="text-sm text-muted-foreground">正在生成回答…</p>
        )}
      </div>

      {traceId && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="truncate" title={traceId}>
            Trace: {traceId}
          </span>
          <button
            type="button"
            aria-label="复制轨迹标识"
            onClick={() => void copyTrace()}
            className="shrink-0 rounded-md border border-border px-2 py-0.5 transition-colors hover:bg-muted hover:text-foreground"
          >
            {COPY_LABEL[copyState]}
          </button>
        </div>
      )}

      {error && (
        <div
          role="alert"
          className="rounded-md border border-danger bg-danger-subtle px-3 py-2 text-sm text-danger"
        >
          {error}
        </div>
      )}

      <div className="flex items-end gap-2">
        <textarea
          rows={2}
          value={value}
          aria-label="测试消息"
          placeholder="输入测试消息，Enter 发送，Shift+Enter 换行"
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          className="max-h-40 flex-1 resize-none rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring"
        />

        {streaming ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-9 w-9 shrink-0 p-0"
            aria-label="停止生成"
            onClick={abort}
          >
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            className="h-9 w-9 shrink-0 p-0"
            aria-label="发送"
            disabled={value.trim() === ""}
            onClick={submit}
          >
            <Send className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
