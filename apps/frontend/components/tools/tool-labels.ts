import type { BadgeVariant } from "@/components/ui/badge";

/** Human labels for tool types. */
export const TOOL_TYPE_LABEL: Record<string, string> = {
  http: "HTTP",
  database: "数据库",
  rpa: "RPA",
  webhook: "Webhook",
  mcp: "MCP",
};

/** Risk level labels + badge variants (low/medium/high). */
export const RISK_LEVEL_LABEL: Record<string, string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
};

export const RISK_LEVEL_VARIANT: Record<string, BadgeVariant> = {
  low: "success",
  medium: "warning",
  high: "danger",
};

/** Permission mode labels + badge variants (auto/confirm/disabled). */
export const PERMISSION_MODE_LABEL: Record<string, string> = {
  auto: "自动执行",
  confirm: "需确认",
  disabled: "已禁用",
};

export const PERMISSION_MODE_VARIANT: Record<string, BadgeVariant> = {
  auto: "success",
  confirm: "warning",
  disabled: "default",
};

/** Tool status labels + badge variants (active/disabled/archived). */
export const TOOL_STATUS_LABEL: Record<string, string> = {
  active: "启用",
  disabled: "禁用",
  archived: "已归档",
};

export const TOOL_STATUS_VARIANT: Record<string, BadgeVariant> = {
  active: "success",
  disabled: "warning",
  archived: "default",
};

/** Auth type labels for the config form. */
export const AUTH_TYPE_LABEL: Record<string, string> = {
  none: "无鉴权",
  api_key: "API Key",
  bearer: "Bearer Token",
  basic: "Basic",
  oauth2: "OAuth 2.0",
};
