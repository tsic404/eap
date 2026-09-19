"use client";

import { useParams } from "next/navigation";

import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { useConversation } from "@/lib/use-conversations";

import { ChatWindow } from "./chat-window";

/** Conversation detail shell: loads the conversation, then renders the chat. */
export function ConversationDetailPage() {
  const params = useParams<{ id: string }>();
  const conversationId = params.id;
  const { data: conversation, error, isLoading, mutate } = useConversation(conversationId);

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Skeleton className="h-full w-full" />
      </div>
    );
  }

  if (error || !conversation) {
    return (
      <div className="flex h-screen items-center justify-center p-6">
        <ErrorBanner
          title="会话不存在或加载失败"
          description="该会话可能已被删除，或没有访问权限"
          onRetry={() => void mutate()}
        />
      </div>
    );
  }

  return (
    <ChatWindow
      conversationId={conversation.id}
      agentId={conversation.agentId}
      agentName={conversation.agentName}
    />
  );
}
