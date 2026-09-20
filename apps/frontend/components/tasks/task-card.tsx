"use client";

import { useAuth } from "@/components/auth/auth-context";
import { useRole } from "@/components/auth/role-context";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { formatDateTime } from "@/lib/formatters";
import {
  TASK_PRIORITY_LABEL,
  TASK_PRIORITY_VARIANT,
  TASK_STATUS_LABEL,
  TASK_STATUS_VARIANT,
  TASK_TYPE_LABEL,
  TASK_TYPE_VARIANT,
} from "@/lib/task-labels";
import {
  canTransition,
  isTaskAdminRole,
  type Task,
} from "@/lib/task-types";

import { ApproveButton } from "./task-approve-button";
import { RejectButton } from "./task-reject-button";
import { RetryButton } from "./task-retry-button";

export interface TaskCardProps {
  task: Task;
  onAction: () => void;
}

/**
 * Creator label. The task API exposes only `creator_id` (no name join), so a
 * creator whose identity is not the current user falls back to the first 8
 * characters of the UUID until the API is extended.
 */
function resolveCreatorLabel(task: Task, currentUserId: string | null): string {
  if (currentUserId != null && task.creator_id === currentUserId) return "我";
  return task.creator_id.slice(0, 8);
}

/** One task card: title, creator + time, type/priority/status tags, actions. */
export function TaskCard({ task, onAction }: TaskCardProps) {
  const { user } = useAuth();
  const role = useRole();

  const isAdmin = isTaskAdminRole(role);
  const isOwner =
    user != null &&
    (task.creator_id === user.id || task.assignee_id === user.id);

  // Button visibility = VALID_TRANSITIONS ∩ role/ownership (§14.1, §P1-5).
  const canApprove = canTransition(task.status, "approved") && isAdmin;
  const canReject = canTransition(task.status, "rejected") && isAdmin;
  // `approved → executing` is the worker's edge; only `failed → executing` is
  // user-driven, so retry is additionally scoped to the failed status.
  const canRetry =
    canTransition(task.status, "executing") &&
    task.status === "failed" &&
    (isAdmin || isOwner);

  return (
    <Card variant="hover" className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-2">
          <h3 className="truncate text-base font-semibold text-foreground">
            {task.title}
          </h3>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={TASK_TYPE_VARIANT[task.type] ?? "default"}>
              {TASK_TYPE_LABEL[task.type] ?? task.type}
            </Badge>
            <Badge
              variant={TASK_PRIORITY_VARIANT[task.priority] ?? "default"}
            >
              {TASK_PRIORITY_LABEL[task.priority] ?? task.priority}
            </Badge>
            <Badge variant={TASK_STATUS_VARIANT[task.status] ?? "default"}>
              {TASK_STATUS_LABEL[task.status] ?? task.status}
            </Badge>
          </div>
        </div>
        <div className="shrink-0 text-right text-xs text-muted-foreground">
          <p>{resolveCreatorLabel(task, user?.id ?? null)}</p>
          <p>{formatDateTime(task.created_at)}</p>
        </div>
      </div>

      {(canApprove || canReject || canRetry) && (
        <div className="mt-4 flex items-center gap-2">
          {canApprove && (
            <ApproveButton taskId={task.id} onSuccess={onAction} />
          )}
          {canReject && <RejectButton taskId={task.id} onSuccess={onAction} />}
          {canRetry && <RetryButton taskId={task.id} onSuccess={onAction} />}
        </div>
      )}
    </Card>
  );
}
