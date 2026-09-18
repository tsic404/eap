import { AlertCircle, RotateCcw } from "lucide-react";

import { cn } from "@/lib/utils";

import { Button } from "./button";

export interface ErrorBannerProps {
  title?: string;
  description?: string;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
}

/** Inline error state with an optional retry action (§20.1.5). */
export function ErrorBanner({
  title = "加载失败",
  description,
  onRetry,
  retryLabel = "重试",
  className,
}: ErrorBannerProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col gap-3 rounded-lg border border-border bg-danger-subtle p-4",
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-danger" />
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium text-foreground">{title}</p>
          {description && (
            <p className="text-sm text-muted-foreground">{description}</p>
          )}
        </div>
      </div>
      {onRetry && (
        <Button
          variant="outline"
          size="sm"
          className="self-start"
          onClick={onRetry}
        >
          <RotateCcw className="h-4 w-4" />
          {retryLabel}
        </Button>
      )}
    </div>
  );
}
