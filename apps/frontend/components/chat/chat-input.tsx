"use client";

import { useRef, useState } from "react";
import { Paperclip, Send, Square, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useToast } from "@/components/ui/toast";
import { uploadChatFile } from "@/lib/conversation-service";
import type { MessageFile, MessageFileType } from "@/lib/conversation-types";
import { extractApiErrorMessage } from "@/lib/platform-service";

const MAX_ATTACHMENTS = 5;
const MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024;
const ACCEPTED_FILE_TYPES = ".pdf,.docx,.xlsx,.txt,.md,.csv,.json,.html";
const ALLOWED_EXTENSIONS = ["pdf", "docx", "xlsx", "txt", "md", "csv", "json", "html"] as const;

interface Attachment {
  id: string;
  name: string;
  type: MessageFileType;
}

export interface ChatInputProps {
  streaming: boolean;
  rateLimitSeconds: number;
  /** Disables the composer while history is loading. */
  disabled?: boolean;
  onSend: (query: string, files: MessageFile[]) => Promise<boolean>;
  onStop: () => void;
}

function validateFile(file: File): string | null {
  const dotIndex = file.name.lastIndexOf(".");
  const ext = dotIndex === -1 ? "" : file.name.slice(dotIndex + 1).toLowerCase();
  if (!(ALLOWED_EXTENSIONS as readonly string[]).includes(ext)) {
    return `不支持的文件类型：.${ext}`;
  }
  if (file.size === 0) {
    return "文件为空";
  }
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return "文件超过 15 MB 限制";
  }
  return null;
}

/** Composer: textarea + attachment upload + send/stop, with a 429 cooldown. */
export function ChatInput({
  streaming,
  rateLimitSeconds,
  disabled = false,
  onSend,
  onStop,
}: ChatInputProps) {
  const { toast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const limited = rateLimitSeconds > 0;

  const addFiles = async (files: File[]) => {
    const room = MAX_ATTACHMENTS - attachments.length;
    if (room <= 0) {
      toast({ type: "warning", title: `最多附加 ${MAX_ATTACHMENTS} 个文件` });
      return;
    }

    if (files.length > room) {
      toast({
        type: "warning",
        title: `最多附加 ${MAX_ATTACHMENTS} 个文件，已忽略 ${files.length - room} 个`,
      });
    }

    const valid: File[] = [];
    for (const file of files.slice(0, room)) {
      const error = validateFile(file);
      if (error) {
        toast({ type: "error", title: file.name, description: error });
      } else {
        valid.push(file);
      }
    }
    if (valid.length === 0) return;

    setUploading(true);
    try {
      for (const file of valid) {
        try {
          const uploaded = await uploadChatFile(file);
          setAttachments((previous) => [
            ...previous,
            { id: uploaded.id, name: uploaded.name, type: uploaded.type },
          ]);
        } catch (uploadError) {
          toast({
            type: "error",
            title: `${file.name} 上传失败`,
            description: extractApiErrorMessage(uploadError),
          });
        }
      }
    } finally {
      setUploading(false);
    }
  };

  const removeAttachment = (id: string) => {
    setAttachments((previous) => previous.filter((attachment) => attachment.id !== id));
  };

  const onInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length > 0) void addFiles(files);
  };

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || streaming || limited || uploading || disabled) return;
    const files = attachments.map(({ id, type }) => ({ id, type }));
    // Clear the composer only once the send actually committed; a rejected
    // send (429 / HTTP error) keeps the text and attachments so the user can
    // retry without re-uploading.
    void onSend(trimmed, files).then((sent) => {
      if (!sent) return;
      setValue("");
      setAttachments([]);
    });
  };

  return (
    <div className="border-t border-border bg-background p-3">
      {attachments.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {attachments.map((attachment) => (
            <div
              key={attachment.id}
              className="flex items-center gap-1 rounded-md border border-border bg-muted px-2 py-1 text-xs text-foreground"
            >
              <span className="max-w-[12rem] truncate">{attachment.name}</span>
              <button
                type="button"
                aria-label={`移除 ${attachment.name}`}
                onClick={() => removeAttachment(attachment.id)}
                className="text-muted-foreground hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-end gap-2">
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED_FILE_TYPES}
          className="hidden"
          onChange={onInputChange}
        />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-9 w-9 shrink-0 p-0"
          aria-label="添加附件"
          disabled={limited || streaming || uploading || disabled}
          onClick={() => inputRef.current?.click()}
        >
          {uploading ? <Spinner size="sm" /> : <Paperclip className="h-4 w-4" />}
        </Button>

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
            disabled={limited || uploading || disabled || value.trim() === ""}
            onClick={submit}
          >
            <Send className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
