"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { MessageSquare, Plus, Trash2 } from "lucide-react";

import { ConfirmDialog } from "@/components/agents/confirm-dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { ROUTES } from "@/lib/api-routes";
import {
  createConversation,
  deleteConversation,
  restoreConversation,
} from "@/lib/conversation-service";
import type { Conversation } from "@/lib/conversation-types";
import { formatDateTime } from "@/lib/formatters";
import { useConversations } from "@/lib/use-conversations";

import { NewConversationDialog } from "./new-conversation-dialog";

// An undo is a deliberate action on a prompt the user must first read, so the
// affordance outlives the default notification lifetime.
const UNDO_WINDOW_MS = 10_000;

function ListSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 5 }).map((_, index) => (
        <Skeleton key={index} className="h-16 w-full" />
      ))}
    </div>
  );
}

/** Recent conversation list (`/user/conversations`) with its new-conversation entry. */
export function ConversationListPage() {
  const router = useRouter();
  const { data, error, isLoading, mutate } = useConversations();
  const { toast } = useToast();
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [creatingAgentId, setCreatingAgentId] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);

  // Creation keeps the user on this page on failure (C-123): a closable prompt
  // appears inside the picker, which stays open for another attempt.
  const startConversation = async (agentId: string) => {
    if (creatingAgentId) return;
    setCreateError(null);
    setCreatingAgentId(agentId);
    try {
      const conversation = await createConversation(agentId);
      router.push(ROUTES.conversationDetail(conversation.id));
    } catch {
      setCreateError("新建会话失败，请重试");
      setCreatingAgentId(null);
    }
  };

  const undoDelete = async (conversation: Conversation) => {
    try {
      await restoreConversation(conversation.id);
      await mutate();
      toast({ type: "success", title: "会话已恢复" });
    } catch {
      // A failed undo means the list may no longer match the server (e.g. the
      // conversation was already restored elsewhere), so re-read it.
      await mutate();
      toast({ type: "error", title: "撤销失败，请重试" });
    }
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    const conversation = pendingDelete;
    setDeleting(true);
    try {
      await deleteConversation(conversation.id);
      await mutate();
      toast({
        type: "success",
        title: "会话已删除",
        duration: UNDO_WINDOW_MS,
        action: { label: "撤销", onClick: () => void undoDelete(conversation) },
      });
    } catch {
      toast({ type: "error", title: "删除失败，请重试" });
    } finally {
      setDeleting(false);
      setPendingDelete(null);
    }
  };

  const items = data?.items ?? [];

  let content: React.ReactNode;
  if (isLoading) {
    content = <ListSkeleton />;
  } else if (error) {
    content = (
      <ErrorBanner
        title="加载会话失败"
        description="无法获取会话列表，请重试"
        onRetry={() => void mutate()}
      />
    );
  } else if (items.length === 0) {
    content = (
      <EmptyState
        icon={<MessageSquare className="h-6 w-6" />}
        title="暂无会话"
        description="点击上方「新建会话」，选定智能体后开始对话"
      />
    );
  } else {
    content = (
      <div className="space-y-3">
        {items.map((conversation) => (
          <Card key={conversation.id} className="flex items-center gap-2">
            <Link
              href={ROUTES.conversationDetail(conversation.id)}
              className="flex min-w-0 flex-1 flex-col gap-1 p-4"
            >
              <span className="truncate text-sm font-medium text-foreground">
                {conversation.title ?? "未命名会话"}
              </span>
              <span className="text-xs text-muted-foreground">
                {conversation.agentName ?? "智能体"} ·{" "}
                {formatDateTime(conversation.updatedAt)}
              </span>
            </Link>
            <button
              type="button"
              aria-label="删除会话"
              onClick={() => setPendingDelete(conversation)}
              className="mr-4 flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-danger"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </Card>
        ))}
      </div>
    );
  }

  return (
    <>
      <div className="mb-4 flex justify-end">
        <Button onClick={() => setPickerOpen(true)} loading={creatingAgentId !== null}>
          <Plus className="h-4 w-4" />
          新建会话
        </Button>
      </div>

      {content}

      {pickerOpen && (
        <NewConversationDialog
          pendingAgentId={creatingAgentId}
          createError={createError}
          onDismissError={() => setCreateError(null)}
          onClose={() => {
            setPickerOpen(false);
            setCreateError(null);
          }}
          onSelect={(agentId) => void startConversation(agentId)}
        />
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="删除会话"
        description={
          pendingDelete
            ? `确定要删除「${pendingDelete.title ?? "未命名会话"}」吗？删除后可在提示中撤销。`
            : ""
        }
        confirmLabel="删除"
        danger
        loading={deleting}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void confirmDelete()}
      />
    </>
  );
}
