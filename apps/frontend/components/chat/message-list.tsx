"use client";

import { MessageSquare } from "lucide-react";
import { Virtuoso } from "react-virtuoso";

import { EmptyState } from "@/components/ui/empty-state";
import type { ChatMessage } from "@/lib/conversation-types";

import { MessageBubble } from "./message-bubble";

export interface MessageListProps {
  messages: ChatMessage[];
  streaming: boolean;
}

/** Virtualized message transcript (react-virtuoso) for 1000+ message sessions. */
export function MessageList({ messages, streaming }: MessageListProps) {
  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <EmptyState
          icon={<MessageSquare className="h-6 w-6" />}
          title="开始对话"
          description="在下方输入消息，向智能体提问"
        />
      </div>
    );
  }

  return (
    <Virtuoso
      style={{ flex: 1 }}
      data={messages}
      computeItemKey={(_, message) => message.id}
      followOutput={(isAtBottom) => (isAtBottom ? "smooth" : false)}
      itemContent={(index, message) => (
        <div className="px-4 py-2">
          <MessageBubble
            message={message}
            streaming={
              streaming &&
              message.role === "assistant" &&
              index === messages.length - 1
            }
          />
        </div>
      )}
    />
  );
}
