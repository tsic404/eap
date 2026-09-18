"use client";

import { useEffect } from "react";
import * as Sentry from "@sentry/nextjs";

import { Button } from "./button";

export interface RouteErrorViewProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Shared fallback for App Router `error.tsx` boundaries. Each route segment's
 * `error.tsx` delegates here so a route-level failure never unmounts the whole
 * tree. The raw error message is never shown to users (it may leak internals);
 * the real error is reported to Sentry instead.
 */
export function RouteErrorView({ error, reset }: RouteErrorViewProps) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-4 p-6 text-center">
      <div className="flex flex-col gap-1">
        <p className="text-lg font-semibold text-foreground">页面出错了</p>
        <p className="text-sm text-muted-foreground">
          发生了未知错误，请稍后重试。
        </p>
        {error.digest && (
          <p className="text-xs text-muted-foreground">错误码：{error.digest}</p>
        )}
      </div>
      <Button variant="outline" onClick={reset}>
        重试
      </Button>
    </div>
  );
}
