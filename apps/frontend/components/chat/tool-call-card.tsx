"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import type { RunStatus, ToolCallRun } from "@/lib/conversation-types";

const STATUS_VARIANT: Record<RunStatus, BadgeVariant> = {
  success: "success",
  failed: "danger",
  running: "info",
  blocked: "warning",
};

const STATUS_LABEL: Record<RunStatus, string> = {
  success: "成功",
  failed: "失败",
  running: "运行中",
  blocked: "已阻塞",
};

function SummaryBlock({ label, content }: { label: string; content: string }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-muted-foreground">{label}</p>
      <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded bg-muted p-2 font-mono text-xs text-foreground">
        {content}
      </pre>
    </div>
  );
}

/** Collapsible tool-call step: status badge up front, request/response on expand. */
export function ToolCallCard({ call }: { call: ToolCallRun }) {
  const [open, setOpen] = useState(false);
  const hasBody = Boolean(call.requestSummary || call.responseSummary);

  return (
    <div className="rounded-md border border-border bg-background">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 p-3 text-left"
      >
        <Wrench className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
          {call.toolName}
        </span>
        <Badge variant={STATUS_VARIANT[call.status]}>
          {call.status === "running" && <Spinner size="sm" />}
          {STATUS_LABEL[call.status]}
        </Badge>
        {hasBody &&
          (open ? (
            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
          ))}
      </button>
      {open && hasBody && (
        <div className="space-y-2 border-t border-border p-3">
          {call.requestSummary && (
            <SummaryBlock label="请求" content={call.requestSummary} />
          )}
          {call.responseSummary && (
            <SummaryBlock label="响应" content={call.responseSummary} />
          )}
        </div>
      )}
    </div>
  );
}
