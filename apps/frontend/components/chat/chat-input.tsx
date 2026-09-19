"use client";

import { useState } from "react";
import { Send, Square } from "lucide-react";

import { Button } from "@/components/ui/button";

export interface ChatInputProps {
  streaming: boolean;
  rateLimitSeconds: number;
  /** Disables the composer while history is loading. */
  disabled?: boolean;
  onSend: (query: string) => void;
  onStop: () => void;
}

/** Composer: textarea + send/stop, with a 429 cooldown that greys the input. */
export function ChatInput({
  streaming,
  rateLimitSeconds,
  disabled = false,
  onSend,
  onStop,
}: ChatInputProps) {
  const [value, setValue] = useState("");
  const limited = rateLimitSeconds > 0;

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || streaming || limited || disabled) return;
    onSend(trimmed);
    setValue("");
  };

  return (
    <div className="border-t border-border bg-background p-3">
      <div className="flex items-end gap-2">
        <textarea
          rows={1}
          value={value}
          disabled={limited || disabled}
          placeholder={
            limited
              ? `${rateLimitSeconds} 秒后可发送`
              : disabled
                ? "正在加载历史消息…"
                : "输入消息，Enter 发送，Shift+Enter 换行"
          }
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          className="max-h-40 flex-1 resize-none rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
        />

        {streaming ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-9 w-9 shrink-0 p-0"
            aria-label="停止生成"
            onClick={onStop}
          >
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            className="h-9 w-9 shrink-0 p-0"
            aria-label="发送"
            disabled={limited || disabled || value.trim() === ""}
            onClick={submit}
          >
            <Send className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
