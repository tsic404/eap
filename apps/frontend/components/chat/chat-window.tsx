"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeft, Bot, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { ROUTES } from "@/lib/api-routes";
import { useStreamChat } from "@/lib/use-stream-chat";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";

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
    historyLoading,
    historyError,
    sendMessage,
    retry,
    retryHistory,
    abort,
    dismissError,
  } = useStreamChat(conversationId, agentId);

  const t = useTranslations("chat");
  const tCommon = useTranslations("common");

  const [tokenNoticeDismissed, setTokenNoticeDismissed] = useState(false);
  const showTokenNotice = truncationNotice && !tokenNoticeDismissed;

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
        <Link
          href={ROUTES.conversations}
          aria-label={t("back")}
          className="flex h-9 w-9 items-center justify-center rounded-md text-foreground transition-colors hover:bg-muted"
        >
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <Bot className="h-5 w-5 text-primary" />
        <span className="truncate text-sm font-semibold text-foreground">
          {agentName ?? t("defaultAgent")}
        </span>
        {traceId && (
          <span className="ml-auto truncate text-xs text-muted-foreground" title={traceId}>
            Trace: {traceId}
          </span>
        )}
      </header>

      {showTokenNotice && (
        <div className="flex items-start gap-2 border-b border-border bg-warning-subtle px-4 py-2 text-sm text-warning">
          <span className="flex-1">{t("truncationNotice")}</span>
          <button
            type="button"
            aria-label={t("dismissNotice")}
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
            {tCommon("retry")}
          </button>
          <button
            type="button"
            aria-label={t("dismissError")}
            onClick={dismissError}
            className="shrink-0 text-danger hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {historyError ? (
        <div className="flex flex-1 items-center justify-center p-6">
          <ErrorBanner
            title={t("historyFailed")}
            description={t("historyFailedDescription")}
            onRetry={retryHistory}
          />
        </div>
      ) : historyLoading ? (
        <div className="flex flex-1 flex-col gap-3 p-4" aria-busy="true">
          <Skeleton className="h-16 w-2/3" />
          <Skeleton className="ml-auto h-16 w-1/2" />
          <Skeleton className="h-16 w-2/3" />
        </div>
      ) : (
        <MessageList messages={messages} streaming={streaming} />
      )}

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
        disabled={historyLoading}
        onSend={(query, files) => sendMessage(query, files)}
        onStop={abort}
      />
    </div>
  );
}
