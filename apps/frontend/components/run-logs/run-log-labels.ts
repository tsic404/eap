/** Badge variants used for run-log statuses; unknown/absent status falls back to `info`. */
export type RunLogStatusVariant = "success" | "danger" | "info" | "warning";

export const RUN_LOG_STATUS_VARIANT: Record<string, RunLogStatusVariant> = {
  success: "success",
  failed: "danger",
  running: "info",
  blocked: "warning",
};

/** Human labels for run-log status values (success/failed/running/blocked). */
export const RUN_LOG_STATUS_LABEL: Record<string, string> = {
  success: "成功",
  failed: "失败",
  running: "运行中",
  blocked: "已阻塞",
};
