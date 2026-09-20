"use client";

import { Badge } from "@/components/ui/badge";
import type { BadgeVariant } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import type { TraceStep } from "@/lib/run-log-types";
import { cn } from "@/lib/utils";

import { RUN_LOG_STATUS_LABEL, RUN_LOG_STATUS_VARIANT } from "./trace-labels";

/** Circle coloring per badge variant, keeping the trace colors aligned with badges. */
const CIRCLE_VARIANT_CLASSES: Record<BadgeVariant, string> = {
  default: "border-border bg-muted text-muted-foreground",
  success: "border-success bg-success-subtle text-success",
  warning: "border-warning bg-warning-subtle text-warning",
  danger: "border-danger bg-danger-subtle text-danger",
  info: "border-info bg-info-subtle text-info",
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

  // No backend flag marks the active step, so infer it as the highest
  // `stepOrder` running step. `stepOrder` can tie (backend `index` falls back
  // to 0), so the latest array index breaks the tie and keeps a unique winner.
  let currentIndex = -1;
  let currentStepOrder = -1;
  steps.forEach((step, index) => {
    if (step.status === "running" && step.stepOrder >= currentStepOrder) {
      currentStepOrder = step.stepOrder;
      currentIndex = index;
    }
  });

  return (
    <ol className="flex flex-col">
      {steps.map((step, index) => {
        const status = step.status ?? "";
        const variant = RUN_LOG_STATUS_VARIANT[status] ?? "info";
        const label = RUN_LOG_STATUS_LABEL[status] ?? step.status ?? "未知";
        const isLast = index === steps.length - 1;
        const duration = step.latencyMs !== null ? `${step.latencyMs} ms` : "—";
        const isRunning = status === "running";
        const isBlocked = status === "blocked";
        const isCurrent = index === currentIndex;

        return (
          <li key={`${step.stepOrder}-${index}`} className="flex gap-4">
            <div className="flex flex-col items-center">
              <span className="relative flex h-8 w-8 shrink-0 items-center justify-center">
                {isRunning && (
                  <span
                    aria-hidden
                    className="absolute inset-0 animate-spin rounded-full border-2 border-transparent border-t-info"
                  />
                )}
                {isCurrent && (
                  <span
                    aria-hidden
                    className="absolute inset-0 rounded-full border-2 border-info animate-ping"
                  />
                )}
                <span
                  aria-hidden
                  className={cn(
                    "flex h-full w-full items-center justify-center rounded-full border text-sm font-medium",
                    CIRCLE_VARIANT_CLASSES[variant],
                    isBlocked && "animate-pulse",
                  )}
                >
                  {step.stepOrder + 1}
                </span>
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
