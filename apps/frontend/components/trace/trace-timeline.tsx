"use client";

import type { BadgeVariant } from "@/components/ui/badge";
import { formatDuration } from "@/lib/formatters";
import type { TraceStep } from "@/lib/run-log-types";
import { cn } from "@/lib/utils";

import { runLogStatusLabel, runLogStatusVariant } from "./trace-labels";

export interface TraceTimelineProps {
  steps: TraceStep[];
  className?: string;
}

const CIRCLE_CLASSES: Record<BadgeVariant, string> = {
  default: "bg-muted text-muted-foreground",
  success: "bg-success-subtle text-success",
  warning: "bg-warning-subtle text-warning",
  danger: "bg-danger-subtle text-danger",
  info: "bg-info-subtle text-info",
};

const STATUS_TEXT_CLASSES: Record<BadgeVariant, string> = {
  default: "text-muted-foreground",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  info: "text-info",
};

/** Vertical step timeline: numbered circles colored by status + latency. */
export function TraceTimeline({ steps, className }: TraceTimelineProps) {
  return (
    <ol className={cn("flex flex-col", className)}>
      {steps.map((step, index) => {
        const variant = runLogStatusVariant(step.status);
        const isLast = index === steps.length - 1;
        return (
          <li
            key={step.stepOrder}
            className="relative flex gap-3 pb-6 last:pb-0"
          >
            {!isLast && (
              <span
                aria-hidden
                className="absolute bottom-0 left-[15px] top-9 w-px bg-border"
              />
            )}
            <span
              className={cn(
                "z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold",
                CIRCLE_CLASSES[variant],
              )}
            >
              {step.stepOrder + 1}
            </span>
            <div className="flex min-w-0 flex-1 flex-col gap-1 pt-0.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="text-sm font-medium text-foreground">
                  {step.name}
                </span>
                {step.type && (
                  <span className="text-xs text-muted-foreground">
                    {step.type}
                  </span>
                )}
                {step.latencyMs != null && (
                  <span className="text-xs text-muted-foreground">
                    {formatDuration(step.latencyMs)}
                  </span>
                )}
                <span
                  className={cn("text-xs", STATUS_TEXT_CLASSES[variant])}
                >
                  {runLogStatusLabel(step.status)}
                </span>
              </div>
              {step.detail && (
                <p className="text-sm text-muted-foreground">{step.detail}</p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
