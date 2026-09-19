"use client";

import { useState } from "react";
import Link from "next/link";
import { MessageSquare, Trash2 } from "lucide-react";

import { ConfirmDialog } from "@/components/agents/confirm-dialog";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { ROUTES } from "@/lib/api-routes";
import { deleteConversation } from "@/lib/conversation-service";
import type { Conversation } from "@/lib/conversation-types";
import { formatDateTime } from "@/lib/formatters";
import { useConversations } from "@/lib/use-conversations";

function ListSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 5 }).map((_, index) => (
        <Skeleton key={index} className="h-16 w-full" />
      ))}
    </div>
  );
}

/** Recent conversation list (`/user/conversations`). */
export function ConversationListPage() {
  const { data, error, isLoading, mutate } = useConversations();
  const { toast } = useToast();
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState(false);

  if (isLoading) return <ListSkeleton />;

  if (error) {
    return (
      <ErrorBanner
        title="加载会话失败"
        description="无法获取会话列表，请重试"
        onRetry={() => void mutate()}
      />
    );
  }

  const items = data?.items ?? [];
  if (items.length === 0) {
    return (
      <EmptyState
        icon={<MessageSquare className="h-6 w-6" />}
        title="暂无会话"
        description="从智能体详情发起对话后，会话会展示在这里"
      />
    );
  }

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await deleteConversation(pendingDelete.id);
      await mutate();
      toast({ type: "success", title: "会话已删除" });
    } catch {
      toast({ type: "error", title: "删除失败，请重试" });
    } finally {
      setDeleting(false);
      setPendingDelete(null);
    }
  };

  return (
    <>
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

      <ConfirmDialog
        open={pendingDelete !== null}
        title="删除会话"
        description={
          pendingDelete
            ? `确定要删除「${pendingDelete.title ?? "未命名会话"}」吗？删除后不可恢复。`
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
