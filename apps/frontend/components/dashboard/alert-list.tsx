"use client";

import { AlertCircle, AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import type { DashboardAlert } from "@/lib/dashboard-health";

export interface AlertListProps {
  alerts: DashboardAlert[];
}

const SEVERITY_ICON = {
  warning: AlertTriangle,
  danger: AlertCircle,
} as const;

const SEVERITY_BADGE = {
  warning: "warning",
  danger: "danger",
} as const;

const SEVERITY_LABEL = {
  warning: "警告",
  danger: "严重",
} as const;

const SEVERITY_ICON_CLASS = {
  warning: "text-warning",
  danger: "text-danger",
} as const;

/** List of threshold-triggered alerts, or a quiet empty state. */
export function AlertList({ alerts }: AlertListProps) {
  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <CardTitle>告警</CardTitle>
      </CardHeader>
      {alerts.length === 0 ? (
        <CardContent>
          <EmptyState title="暂无告警" description="当前指标均在正常范围" />
        </CardContent>
      ) : (
        <CardContent className="flex flex-col gap-4">
          {alerts.map((alert) => {
            const Icon = SEVERITY_ICON[alert.severity];
            return (
              <div key={alert.id} className="flex items-start gap-3">
                <Icon
                  className={`mt-0.5 h-4 w-4 shrink-0 ${SEVERITY_ICON_CLASS[alert.severity]}`}
                />
                <div className="flex min-w-0 flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground">
                      {alert.title}
                    </span>
                    <Badge variant={SEVERITY_BADGE[alert.severity]}>
                      {SEVERITY_LABEL[alert.severity]}
                    </Badge>
                  </div>
                  <p className="text-sm text-muted-foreground">
                    {alert.description}
                  </p>
                </div>
              </div>
            );
          })}
        </CardContent>
      )}
    </Card>
  );
}
