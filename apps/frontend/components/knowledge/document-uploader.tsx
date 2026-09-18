"use client";

import { useRef, useState } from "react";
import { UploadCloud } from "lucide-react";

import { useToast } from "@/components/ui/toast";
import { uploadDocument } from "@/lib/knowledge-service";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { cn } from "@/lib/utils";

const MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024;
const ALLOWED_EXTENSIONS = [
  "pdf",
  "docx",
  "xlsx",
  "txt",
  "md",
  "csv",
  "json",
  "html",
] as const;

interface UploadEntry {
  id: string;
  name: string;
  progress: number;
  status: "uploading" | "done" | "error";
  error?: string;
}

function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index === -1 ? "" : name.slice(index + 1).toLowerCase();
}

function isAllowedExtension(name: string): boolean {
  return (ALLOWED_EXTENSIONS as readonly string[]).includes(extensionOf(name));
}

export interface DocumentUploaderProps {
  kbId: string;
  onUploaded: () => void;
}

/** Drag-and-drop document uploader with per-file progress and type validation. */
export function DocumentUploader({ kbId, onUploaded }: DocumentUploaderProps) {
  const { toast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploads, setUploads] = useState<UploadEntry[]>([]);

  const validate = (file: File): string | null => {
    if (!isAllowedExtension(file.name)) {
      return `不支持的文件类型：.${extensionOf(file.name)}`;
    }
    if (file.size === 0) {
      return "文件为空";
    }
    if (file.size > MAX_FILE_SIZE_BYTES) {
      return "文件超过 15 MB 限制";
    }
    return null;
  };

  const uploadFiles = async (files: File[]) => {
    const valid = files.filter((file) => {
      const error = validate(file);
      if (error) {
        toast({ type: "error", title: file.name, description: error });
        return false;
      }
      return true;
    });
    if (valid.length === 0) return;

    for (const file of valid) {
      // Per-operation id: file metadata can repeat (re-selecting the same file),
      // which would collide React keys and cross-wire progress between entries.
      const id = crypto.randomUUID();
      setUploads((previous) => [
        ...previous,
        { id, name: file.name, progress: 0, status: "uploading" },
      ]);
      try {
        await uploadDocument(kbId, file, (percent) => {
          setUploads((previous) =>
            previous.map((entry) =>
              entry.id === id ? { ...entry, progress: percent } : entry,
            ),
          );
        });
        setUploads((previous) =>
          previous.map((entry) =>
            entry.id === id ? { ...entry, progress: 100, status: "done" } : entry,
          ),
        );
        toast({ type: "success", title: `${file.name} 上传成功` });
      } catch (uploadError) {
        const message = extractApiErrorMessage(uploadError);
        setUploads((previous) =>
          previous.map((entry) =>
            entry.id === id ? { ...entry, status: "error", error: message } : entry,
          ),
        );
        toast({
          type: "error",
          title: `${file.name} 上传失败`,
          description: message,
        });
      }
    }
    onUploaded();
  };

  const onDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0) void uploadFiles(files);
  };

  const onInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (files.length > 0) void uploadFiles(files);
    event.target.value = "";
  };

  return (
    <div className="flex flex-col gap-3">
      <div
        role="button"
        tabIndex={0}
        aria-label="拖拽文件到此处或点击上传"
        onClick={() => inputRef.current?.click()}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            inputRef.current?.click();
          }
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-border bg-muted/40 px-6 py-8 text-center transition-colors",
          dragging && "border-primary bg-primary-subtle",
        )}
      >
        <UploadCloud className="h-8 w-8 text-muted-foreground" />
        <p className="text-sm text-foreground">拖拽文件到此处，或点击上传</p>
        <p className="text-xs text-muted-foreground">
          支持 pdf / docx / xlsx / txt / md / csv / json / html，单文件不超过 15 MB
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={onInputChange}
        />
      </div>

      {uploads.length > 0 && (
        <ul className="flex flex-col gap-2">
          {uploads.map((entry) => (
            <li
              key={entry.id}
              className="flex items-center gap-3 rounded-md border border-border px-3 py-2"
            >
              <span className="min-w-0 flex-1 truncate text-sm text-foreground">
                {entry.name}
              </span>
              {entry.status === "uploading" && (
                <progress
                  value={entry.progress}
                  max={100}
                  className="h-2 w-32 appearance-none rounded-full [&::-webkit-progress-bar]:rounded-full [&::-webkit-progress-bar]:bg-muted [&::-webkit-progress-value]:rounded-full [&::-webkit-progress-value]:bg-primary [&::-moz-progress-bar]:rounded-full [&::-moz-progress-bar]:bg-primary"
                />
              )}
              {entry.status === "done" && (
                <span className="text-sm text-success">完成</span>
              )}
              {entry.status === "error" && (
                <span className="text-sm text-danger" title={entry.error}>
                  失败
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
