"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeft, Bot, X } from "lucide-react";

import { ROUTES } from "@/lib/api-routes";
import { useStreamChat } from "@/lib/use-stream-chat";

import { ChatInput } from "./chat-input";
import { CitationCard } from "./citation-card";
import { MessageList } from "./message-list";
import { ToolCallCard } from "./tool-call-card";
import { WorkflowProgress } from "./workflow-progress";

export interface ChatWindowProps {
  conversationId: string;
  agentId: string;
  agentName: string | null;
}

/** Full-height chat surface: header, virtualized transcript, turn artifacts, input. */
export function ChatWindow({ conversationId, agentId, agentName }: ChatWindowProps) {
  const {
    messages,
    citations,
    toolCalls,
    workflow,
    traceId,
    truncationNotice,
    streaming,
    error,
    rateLimitSeconds,
    sendMessage,
    retry,
    abort,
    dismissError,
  } = useStreamChat(conversationId, agentId);

  const [tokenNoticeDismissed, setTokenNoticeDismissed] = useState(false);
  const showTokenNotice = truncationNotice && !tokenNoticeDismissed;

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
        <Link
          href={ROUTES.conversations}
          aria-label="返回会话列表"
          className="flex h-9 w-9 items-center justify-center rounded-md text-foreground transition-colors hover:bg-muted"
        >
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <Bot className="h-5 w-5 text-primary" />
        <span className="truncate text-sm font-semibold text-foreground">
          {agentName ?? "智能体"}
        </span>
        {traceId && (
          <span className="ml-auto truncate text-xs text-muted-foreground" title={traceId}>
            Trace: {traceId}
          </span>
        )}
      </header>

      {showTokenNotice && (
        <div className="flex items-start gap-2 border-b border-border bg-warning-subtle px-4 py-2 text-sm text-warning">
          <span className="flex-1">对话较长，较早的消息可能未被纳入上下文</span>
          <button
            type="button"
            aria-label="关闭提示"
            onClick={() => setTokenNoticeDismissed(true)}
            className="text-warning hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {error && (
        <div
          role="alert"
          className="flex items-start gap-2 border-b border-border bg-danger-subtle px-4 py-2 text-sm text-danger"
        >
          <span className="flex-1">{error}</span>
          <button
            type="button"
            onClick={retry}
            className="shrink-0 font-medium underline underline-offset-2 hover:text-foreground"
          >
            重试
          </button>
          <button
            type="button"
            aria-label="关闭错误提示"
            onClick={dismissError}
            className="shrink-0 text-danger hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      <MessageList messages={messages} streaming={streaming} />

      {(workflow || toolCalls.length > 0 || citations.length > 0) && (
        <div className="max-h-40 shrink-0 space-y-2 overflow-y-auto border-t border-border px-4 py-2">
          {workflow && <WorkflowProgress workflow={workflow} />}
          {toolCalls.map((call) => (
            <ToolCallCard key={call.id} call={call} />
          ))}
          {citations.map((citation, index) => (
            <CitationCard key={`${citation.sourceName}-${index}`} citation={citation} />
          ))}
        </div>
      )}

      <ChatInput
        streaming={streaming}
        rateLimitSeconds={rateLimitSeconds}
        onSend={(query) => void sendMessage(query)}
        onStop={abort}
      />
    </div>
  );
}
