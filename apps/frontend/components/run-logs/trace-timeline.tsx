"use client";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import type { TraceStep } from "@/lib/run-log-types";
import { cn } from "@/lib/utils";

import {
  RUN_LOG_STATUS_LABEL,
  RUN_LOG_STATUS_VARIANT,
  type RunLogStatusVariant,
} from "./run-log-labels";

/** Circle coloring per badge variant, keeping the trace colors aligned with badges. */
const CIRCLE_VARIANT_CLASSES: Record<RunLogStatusVariant, string> = {
  success: "border-success bg-success-subtle text-success",
  danger: "border-danger bg-danger-subtle text-danger",
  info: "border-info bg-info-subtle text-info",
  warning: "border-warning bg-warning-subtle text-warning",
};

export interface TraceTimelineProps {
  steps: TraceStep[];
}

/** Vertical timeline of a run's workflow steps, colored by step status. */
export function TraceTimeline({ steps }: TraceTimelineProps) {
  if (steps.length === 0) {
    return (
      <EmptyState title="暂无步骤" description="该运行记录没有可展示的步骤" />
    );
  }

  return (
    <ol className="flex flex-col">
      {steps.map((step, index) => {
        const status = step.status ?? "";
        const variant = RUN_LOG_STATUS_VARIANT[status] ?? "info";
        const label = RUN_LOG_STATUS_LABEL[status] ?? step.status ?? "未知";
        const isLast = index === steps.length - 1;
        const duration = step.latencyMs !== null ? `${step.latencyMs} ms` : "—";

        return (
          <li key={`${step.stepOrder}-${index}`} className="flex gap-4">
            <div className="flex flex-col items-center">
              <span
                aria-hidden
                className={cn(
                  "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-sm font-medium",
                  CIRCLE_VARIANT_CLASSES[variant],
                )}
              >
                {step.stepOrder + 1}
              </span>
              {!isLast && (
                <span aria-hidden className="mt-2 w-px flex-1 bg-border" />
              )}
            </div>
            <div className="flex min-w-0 flex-1 flex-col gap-1 pb-6">
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="truncate text-sm font-medium text-foreground">
                    {step.name}
                  </span>
                  <Badge variant={variant}>{label}</Badge>
                </div>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {duration}
                </span>
              </div>
              {step.type && (
                <span className="text-xs text-muted-foreground">{step.type}</span>
              )}
              {step.detail && (
                <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                  {step.detail}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
