/**
 * Task domain types mirroring the backend `app/schemas/task.py` `TaskRead` DTO
 * and the `app/services/task_service.py` state machine (§14.1). Task resources
 * are serialized in snake_case (the backend DTO keeps the DB column names), so
 * these shapes follow the wire contract verbatim.
 */

export const TASK_STATUSES = [
  "pending",
  "approved",
  "rejected",
  "executing",
  "completed",
  "failed",
  "cancelled",
] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

export const TASK_PRIORITIES = ["high", "normal", "low"] as const;
export type TaskPriority = (typeof TASK_PRIORITIES)[number];

export const TASK_TYPES = [
  "tool_approval",
  "knowledge_index",
  "memory_extraction",
  "audit_log",
] as const;
export type TaskType = (typeof TASK_TYPES)[number];

/**
 * State-machine transition whitelist, mirrored from the backend's
 * `VALID_TRANSITIONS` (§14.1). Frontend button visibility derives from this
 * table plus the role matrix, so a status change lives in one place.
 */
export const VALID_TRANSITIONS: Record<TaskStatus, readonly TaskStatus[]> = {
  pending: ["approved", "rejected", "cancelled"],
  approved: ["executing", "cancelled"],
  rejected: [],
  executing: ["completed", "failed", "cancelled"],
  completed: [],
  failed: ["executing", "cancelled"],
  cancelled: [],
};

/** Roles allowed to approve/reject tasks (backend `ADMIN_ROLES`). */
export const TASK_ADMIN_ROLES = ["platform_admin", "agent_admin"] as const;
export type TaskAdminRole = (typeof TASK_ADMIN_ROLES)[number];

/** Whether `role` may approve/reject tasks. */
export function isTaskAdminRole(role: string): boolean {
  return (TASK_ADMIN_ROLES as readonly string[]).includes(role);
}

/** Whether `status` permits a user-driven transition to `target` (§14.1). */
export function canTransition(status: string, target: TaskStatus): boolean {
  const allowed = VALID_TRANSITIONS[status as TaskStatus];
  return allowed != null && allowed.includes(target);
}

/** One task returned by `GET /api/tasks` (mirrors `TaskRead`). */
export interface Task {
  id: string;
  tenant_id: string;
  creator_id: string;
  assignee_id: string | null;
  type: string;
  title: string;
  priority: string;
  status: string;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error_message: string | null;
  retry_count: number;
  max_retries: number;
  expires_at: string | null;
  created_at: string;
  resolved_at: string | null;
}

/** Cursor-paginated task list returned by `GET /api/tasks`. */
export interface TaskList {
  items: Task[];
  next_cursor: string | null;
}

/** Query parameters accepted by `GET /api/tasks` (§32.7.2). */
export interface TaskListParams {
  status?: string;
  type?: string;
  priority?: string;
  limit?: number;
  cursor?: string;
}
