"use client";

import type { ChatMessage } from "@/lib/conversation-types";
import { cn } from "@/lib/utils";

import { MarkdownText } from "./markdown-text";
import { StreamingText } from "./streaming-text";

export interface MessageBubbleProps {
  message: ChatMessage;
  /** Marks the assistant bubble currently receiving SSE chunks. */
  streaming?: boolean;
}

/** One chat bubble: user (blue, right) vs assistant (white, left). */
export function MessageBubble({ message, streaming = false }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-lg px-4 py-3 text-sm",
          isUser
            ? "bg-primary text-primary-foreground"
            : "border border-border bg-background text-foreground",
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap break-words leading-relaxed">
            {message.content}
          </p>
        ) : streaming ? (
          <StreamingText text={message.content} active />
        ) : (
          <MarkdownText text={message.content} />
        )}
      </div>
    </div>
  );
}
