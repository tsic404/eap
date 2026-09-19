"use client";

import { AlertCircle, AlertTriangle, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  HEALTH_LEVEL_LABEL,
  type CheckStatus,
  type HealthCheck,
  type HealthLevel,
} from "@/lib/dashboard-health";

export interface HealthStatusPanelProps {
  level: HealthLevel;
  checks: HealthCheck[];
}

const LEVEL_ICON = {
  healthy: ShieldCheck,
  degraded: AlertTriangle,
  critical: AlertCircle,
} as const;

const LEVEL_BADGE = {
  healthy: "success",
  degraded: "warning",
  critical: "danger",
} as const;

const LEVEL_ICON_CLASS = {
  healthy: "text-success",
  degraded: "text-warning",
  critical: "text-danger",
} as const;

const CHECK_DOT_CLASS: Record<CheckStatus, string> = {
  ok: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
};

/** Overall system health plus the per-signal checks behind it. */
export function HealthStatusPanel({ level, checks }: HealthStatusPanelProps) {
  const Icon = LEVEL_ICON[level];

  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <CardTitle>系统健康</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted">
            <Icon className={`h-5 w-5 ${LEVEL_ICON_CLASS[level]}`} />
          </span>
          <Badge variant={LEVEL_BADGE[level]}>
            {HEALTH_LEVEL_LABEL[level]}
          </Badge>
        </div>
        <ul className="flex flex-col gap-2">
          {checks.map((check) => (
            <li key={check.label} className="flex items-center gap-3">
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${CHECK_DOT_CLASS[check.status]}`}
              />
              <span className="flex-1 text-sm text-foreground">{check.label}</span>
              <span className="text-sm text-muted-foreground">{check.detail}</span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
