import type { BadgeVariant } from "@/components/ui/badge";

/** Human labels for run-log/trace status values. */
export const RUN_LOG_STATUS_LABEL: Record<string, string> = {
  success: "成功",
  failed: "失败",
  running: "运行中",
  blocked: "已阻塞",
};

export const RUN_LOG_STATUS_VARIANT: Record<string, BadgeVariant> = {
  success: "success",
  failed: "danger",
  running: "info",
  blocked: "warning",
};

/** Status badge variant, defaulting safely for unknown/absent statuses. */
export function runLogStatusVariant(
  status: string | null | undefined,
): BadgeVariant {
  return status ? (RUN_LOG_STATUS_VARIANT[status] ?? "default") : "default";
}

/** Status label, falling back to the raw value when unknown. */
export function runLogStatusLabel(status: string | null | undefined): string {
  if (!status) return "未知";
  return RUN_LOG_STATUS_LABEL[status] ?? status;
}
