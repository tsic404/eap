import type { BadgeVariant } from "@/components/ui/badge";

/**
 * Human labels for knowledge-base / document indexing statuses, using the
 * contract's state-machine words (`indexing → ready | failed`). Documents come
 * back from the API with `completed` as the terminal success state, so that key
 * renders the same word as `ready`.
 */
export const INDEXING_STATUS_LABEL: Record<string, string> = {
  ready: "就绪",
  indexing: "索引中",
  completed: "就绪",
  failed: "失败",
};

export const INDEXING_STATUS_VARIANT: Record<string, BadgeVariant> = {
  ready: "default",
  indexing: "info",
  completed: "success",
  failed: "danger",
};

/** Contract copy for a document whose indexing failed (§3.4 / AC-18). */
export const INDEXING_FAILED_MESSAGE = "索引失败：文件解析错误";

/** Contract copy shown next to the disabled retrieval test while indexing (AC-18). */
export const INDEXING_IN_PROGRESS_MESSAGE = "知识库正在索引中…";

/** Human labels for knowledge-base types. */
export const KB_TYPE_LABEL: Record<string, string> = {
  business: "业务",
};

/** AC-15 wording echoed at the name field on a duplicate-name 409 (`KB_NAME_EXISTS`). */
export const KB_NAME_EXISTS_MESSAGE = "知识库名称已存在";
