"use client";

import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";

import { Spinner } from "@/components/ui/spinner";
import type { WorkflowNodeStatus, WorkflowState } from "@/lib/conversation-types";
import { cn } from "@/lib/utils";

function NodeIcon({ status }: { status: WorkflowNodeStatus }) {
  if (status === "success") return <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />;
  if (status === "failed") return <XCircle className="h-4 w-4 shrink-0 text-danger" />;
  if (status === "stopped") return <Circle className="h-4 w-4 shrink-0 text-muted-foreground" />;
  return <Loader2 className="h-4 w-4 shrink-0 animate-spin text-info" />;
}

/** Workflow node progress list shown while a workflow-driven reply streams. */
export function WorkflowProgress({ workflow }: { workflow: WorkflowState }) {
  if (workflow.nodes.length === 0) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-border bg-background p-3 text-sm text-muted-foreground">
        <Spinner size="sm" />
        正在运行工作流…
      </div>
    );
  }

  return (
    <div className="rounded-md border border-border bg-background p-3">
      <p className="mb-2 text-xs font-medium text-muted-foreground">工作流进度</p>
      <ul className="space-y-2">
        {workflow.nodes.map((node) => (
          <li key={node.id} className="flex items-center gap-2 text-sm">
            <NodeIcon status={node.status} />
            <span
              className={cn(
                "truncate",
                node.status === "running" ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {node.title ?? node.id}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
