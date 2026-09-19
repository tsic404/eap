"use client";

import type { LucideIcon } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";

export interface MetricCardProps {
  label: string;
  value: React.ReactNode;
  icon: LucideIcon;
  hint?: string;
}

/** Single summary metric: icon + label + value, with an optional hint line. */
export function MetricCard({ label, value, icon: Icon, hint }: MetricCardProps) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-4">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-subtle text-primary">
          <Icon className="h-5 w-5" />
        </div>
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-xs text-muted-foreground">{label}</span>
          <span className="text-lg font-semibold text-foreground">{value}</span>
          {hint && (
            <span className="truncate text-xs text-muted-foreground">{hint}</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
