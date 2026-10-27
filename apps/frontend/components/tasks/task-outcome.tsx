"use client";

import { Badge } from "@/components/ui/badge";
import {
  TASK_FAILED_PROMPT,
  TASK_REJECTED_PROMPT,
  TASK_RESULT_TITLE,
  TASK_TIMEOUT_HINT,
} from "@/lib/task-labels";
import {
  resolveTaskOutcome,
  TASK_TIMEOUT_REASON,
  type Task,
} from "@/lib/task-types";

export interface TaskOutcomeProps {
  task: Task;
}

function formatBody(body: unknown): string {
  if (typeof body === "string") return body;
  return JSON.stringify(body, null, 2) ?? "";
}

/**
 * Outcome block on a task card: the executed tool's response, the rejection
 * prompt with its reason, the timeout cancellation, or the failure message.
 * Renders nothing while the task is still in flight.
 */
export function TaskOutcome({ task }: TaskOutcomeProps) {
  const outcome = resolveTaskOutcome(task);
  if (outcome === null) return null;

  if (outcome.kind === "executed") {
    const isSuccess = outcome.statusCode >= 200 && outcome.statusCode < 300;
    return (
      <div className="mt-3 flex flex-col gap-2 rounded-md border border-border bg-muted p-3">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="font-medium text-foreground">
            {TASK_RESULT_TITLE}
          </span>
          <Badge variant={isSuccess ? "success" : "danger"}>
            {outcome.statusCode}
          </Badge>
          {outcome.latencyMs !== null && (
            <span className="text-muted-foreground">
              耗时 {outcome.latencyMs.toFixed(0)} ms
            </span>
          )}
        </div>
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-sm text-foreground">
          {formatBody(outcome.body)}
        </pre>
      </div>
    );
  }

  if (outcome.kind === "expired") {
    return (
      <div className="mt-3 flex flex-col gap-1 rounded-md bg-warning-subtle p-3">
        <p className="text-sm font-medium text-warning">{TASK_TIMEOUT_REASON}</p>
        <p className="text-sm text-muted-foreground">{TASK_TIMEOUT_HINT}</p>
      </div>
    );
  }

  if (outcome.kind === "rejected") {
    return (
      <div className="mt-3 flex flex-col gap-1 rounded-md bg-danger-subtle p-3">
        <p className="text-sm font-medium text-danger">{TASK_REJECTED_PROMPT}</p>
        {outcome.reason !== null && (
          <p className="text-sm text-muted-foreground">
            驳回原因：{outcome.reason}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="mt-3 flex flex-col gap-1 rounded-md bg-danger-subtle p-3">
      <p className="text-sm font-medium text-danger">{TASK_FAILED_PROMPT}</p>
      {outcome.message !== null && (
        <p className="text-sm text-muted-foreground">{outcome.message}</p>
      )}
    </div>
  );
}
