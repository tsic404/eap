"use client";

import { MessageSquare } from "lucide-react";

import { RUN_LOG_STATUS_LABEL, RUN_LOG_STATUS_VARIANT } from "@/components/run-logs/run-log-labels";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import type { RunLog } from "@/lib/agent-types";
import { formatDateTime } from "@/lib/formatters";

/**
 * Embedded debug conversation window. Renders the agent's recent run logs as a
 * chat transcript — user input plus outcome — so an admin can inspect what the
 * agent was asked and how each run ended.
 */
export function MiniChatWindow({ logs }: { logs: RunLog[] }) {
  if (logs.length === 0) {
    return (
      <EmptyState
        icon={<MessageSquare className="h-6 w-6" />}
        title="暂无调试记录"
        description="智能体运行后的对话记录会展示在这里"
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {logs.map((log) => {
        const statusLabel = RUN_LOG_STATUS_LABEL[log.status ?? ""] ?? log.status ?? "未知";
        const statusVariant = RUN_LOG_STATUS_VARIANT[log.status ?? ""] ?? "info";
        return (
          <div
            key={log.traceId}
            className="flex flex-col gap-2 rounded-lg border border-border bg-background p-3"
          >
            <div className="flex items-center justify-between gap-3">
              <Badge variant={statusVariant}>{statusLabel}</Badge>
              <span className="text-xs text-muted-foreground">
                {log.latencyMs !== null && `${log.latencyMs} ms · `}
                {log.createdAt ? formatDateTime(log.createdAt) : ""}
              </span>
            </div>
            <p className="whitespace-pre-wrap break-words text-sm text-foreground">
              {log.input ?? ""}
            </p>
          </div>
        );
      })}
    </div>
  );
}
