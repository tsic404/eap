import type { BadgeVariant } from "@/components/ui/badge";

/** Human labels for knowledge-base / document indexing statuses. */
export const INDEXING_STATUS_LABEL: Record<string, string> = {
  ready: "就绪",
  indexing: "索引中",
  completed: "已完成",
  failed: "失败",
};

export const INDEXING_STATUS_VARIANT: Record<string, BadgeVariant> = {
  ready: "default",
  indexing: "info",
  completed: "success",
  failed: "danger",
};

/** Human labels for knowledge-base types. */
export const KB_TYPE_LABEL: Record<string, string> = {
  business: "业务",
};

/** AC-15 wording echoed at the name field on a duplicate-name 409 (`KB_NAME_EXISTS`). */
export const KB_NAME_EXISTS_MESSAGE = "知识库名称已存在";
