import { Badge } from "@/components/ui/badge";

import {
  PERMISSION_MODE_LABEL,
  PERMISSION_MODE_VARIANT,
  RISK_LEVEL_LABEL,
  RISK_LEVEL_VARIANT,
} from "./tool-labels";

export interface RiskLevelBadgeProps {
  riskLevel: string;
}

/** Colored risk-level badge: low/medium/high → success/warning/danger. */
export function RiskLevelBadge({ riskLevel }: RiskLevelBadgeProps) {
  return (
    <Badge variant={RISK_LEVEL_VARIANT[riskLevel] ?? "default"}>
      {RISK_LEVEL_LABEL[riskLevel] ?? riskLevel}
    </Badge>
  );
}

export interface PermissionModeBadgeProps {
  permissionMode: string;
}

/** Permission-mode badge: auto/confirm/disabled. */
export function PermissionModeBadge({
  permissionMode,
}: PermissionModeBadgeProps) {
  return (
    <Badge variant={PERMISSION_MODE_VARIANT[permissionMode] ?? "default"}>
      {PERMISSION_MODE_LABEL[permissionMode] ?? permissionMode}
    </Badge>
  );
}
