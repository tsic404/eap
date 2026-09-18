"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ToolDetail } from "@/lib/tool-types";

import { PermissionModeBadge, RiskLevelBadge } from "./tool-badges";
import { TOOL_STATUS_LABEL, TOOL_STATUS_VARIANT, TOOL_TYPE_LABEL } from "./tool-labels";

export interface ToolCardProps {
  tool: ToolDetail;
  onClick?: () => void;
}

/** Tool card: name + status + description + risk/permission badges. */
export function ToolCard({ tool, onClick }: ToolCardProps) {
  const typeLabel = TOOL_TYPE_LABEL[tool.type] ?? tool.type;
  const statusLabel = TOOL_STATUS_LABEL[tool.status] ?? tool.status;
  const statusVariant = TOOL_STATUS_VARIANT[tool.status] ?? "default";

  return (
    <Card
      variant={onClick ? "interactive" : "default"}
      onClick={onClick}
      className="flex h-full flex-col"
    >
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-muted text-2xl leading-none">
              🔧
            </span>
            <div className="flex min-w-0 flex-col">
              <CardTitle className="truncate">{tool.name}</CardTitle>
              <span className="text-xs text-muted-foreground">
                {typeLabel} · {tool.tool_id}
              </span>
            </div>
          </div>
          <Badge variant={statusVariant}>{statusLabel}</Badge>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col justify-between gap-3">
        <p className="line-clamp-2 text-sm text-muted-foreground">
          {tool.description ?? "暂无描述"}
        </p>
        <div className="flex items-center gap-2">
          <RiskLevelBadge riskLevel={tool.risk_level} />
          <PermissionModeBadge permissionMode={tool.permission_mode} />
        </div>
      </CardContent>
    </Card>
  );
}
