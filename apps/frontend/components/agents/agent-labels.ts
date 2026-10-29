import type { BadgeVariant } from "@/components/ui/badge";

/** Human labels for agent status values (§32.2.2 `status` enum). */
export const AGENT_STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  testing: "测试中",
  published: "已发布",
  offline: "已下线",
};

/** 状态配色：草稿灰、测试中蓝、已发布绿、已下线红（下线为终态，红色承载撤下语义）。 */
export const AGENT_STATUS_VARIANT: Record<string, BadgeVariant> = {
  draft: "default",
  testing: "info",
  published: "success",
  offline: "danger",
};

/** Human labels for agent type values (`chat`/`agent`/`workflow`/`data`). */
export const AGENT_TYPE_LABEL: Record<string, string> = {
  chat: "对话",
  agent: "智能体",
  workflow: "工作流",
  data: "数据",
};
