"use client";

import { Bot, X } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { cn } from "@/lib/utils";
import { useAllAgents } from "@/lib/use-agents";

export interface NewConversationDialogProps {
  /** Agent whose conversation is being created; the picker is locked meanwhile. */
  pendingAgentId: string | null;
  /** User-visible failure of the last creation attempt; cleared on dismiss. */
  createError: string | null;
  onDismissError: () => void;
  onClose: () => void;
  onSelect: (agentId: string) => void;
}

/**
 * Candidate picker for the conversation-list "new conversation" entry (C-123).
 * Only published agents may start a conversation (C-34 / C-53): the list is
 * filtered here as well, because admin roles receive every status from the API.
 */
export function NewConversationDialog({
  pendingAgentId,
  createError,
  onDismissError,
  onClose,
  onSelect,
}: NewConversationDialogProps) {
  const { data, error, isLoading, mutate } = useAllAgents();
  const candidates = (data ?? []).filter((agent) => agent.status === "published");
  const busy = pendingAgentId !== null;

  return (
    <Modal open onClose={busy ? () => {} : onClose} title="新建会话">
      {createError && (
        <div
          role="alert"
          className="mb-3 flex items-start gap-2 rounded-md border border-border bg-danger-subtle px-3 py-2 text-sm text-danger"
        >
          <span className="flex-1">{createError}</span>
          <button
            type="button"
            aria-label="关闭错误提示"
            onClick={onDismissError}
            className="shrink-0 text-danger hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}
      {isLoading ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
        </div>
      ) : error ? (
        <ErrorBanner
          title="加载智能体失败"
          description={extractApiErrorMessage(error)}
          onRetry={() => void mutate()}
        />
      ) : candidates.length === 0 ? (
        <EmptyState
          icon={<Bot className="h-6 w-6" />}
          title="暂无已发布的智能体"
          description="智能体发布后才能用于新建会话"
        />
      ) : (
        <ul className="flex max-h-80 flex-col gap-1 overflow-y-auto">
          {candidates.map((agent) => (
            <li key={agent.agentId}>
              <button
                type="button"
                disabled={busy}
                onClick={() => onSelect(agent.agentId)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm text-foreground",
                  busy ? "cursor-not-allowed opacity-60" : "hover:bg-muted",
                )}
              >
                <span className="text-lg leading-none">{agent.icon ?? "🤖"}</span>
                <span className="min-w-0 flex-1 truncate">{agent.name}</span>
                {pendingAgentId === agent.agentId && <Spinner size="sm" />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
