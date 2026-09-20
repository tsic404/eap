import type { BadgeVariant } from "@/components/ui/badge";

/** Human labels + badge variants for task statuses (§14.3). */
export const TASK_STATUS_LABEL: Record<string, string> = {
  pending: "待审批",
  approved: "已通过",
  rejected: "已拒绝",
  executing: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export const TASK_STATUS_VARIANT: Record<string, BadgeVariant> = {
  pending: "warning",
  approved: "success",
  rejected: "danger",
  executing: "info",
  completed: "success",
  failed: "danger",
  cancelled: "default",
};

/**
 * Priority labels + badge variants (§14.3: 紧急红/高橙/中蓝/低灰). The backend
 * emits the snake_case values `high`/`normal`; `urgent`/`medium`/`low` are
 * defensive entries so an unknown future value still renders a sensible label.
 */
export const TASK_PRIORITY_LABEL: Record<string, string> = {
  urgent: "紧急",
  high: "高",
  normal: "中",
  medium: "中",
  low: "低",
};

export const TASK_PRIORITY_VARIANT: Record<string, BadgeVariant> = {
  urgent: "danger",
  high: "warning",
  normal: "info",
  medium: "info",
  low: "default",
};

/** Task type labels + badge variants (工具审批橙/索引进度蓝/记忆提取蓝/审计灰). */
export const TASK_TYPE_LABEL: Record<string, string> = {
  tool_approval: "工具审批",
  knowledge_index: "索引进度",
  memory_extraction: "记忆提取",
  audit_log: "审计日志",
};

export const TASK_TYPE_VARIANT: Record<string, BadgeVariant> = {
  tool_approval: "warning",
  knowledge_index: "info",
  memory_extraction: "info",
  audit_log: "default",
};
