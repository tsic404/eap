"use client";

import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { ROUTES } from "@/lib/api-routes";
import { useRunLogDetail } from "@/lib/use-run-logs";

import { RUN_LOG_STATUS_LABEL, RUN_LOG_STATUS_VARIANT } from "./run-log-labels";
import { TraceTimeline } from "./trace-timeline";

/** Run-log detail shell: loads the trace, then renders its step timeline. */
export function RunLogDetailPage() {
  const params = useParams<{ traceId: string }>();
  const traceId = params.traceId;
  const router = useRouter();
  const { data: detail, error, isLoading, mutate } = useRunLogDetail(traceId);

  if (isLoading) {
    return (
      <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-8">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-8">
        <ErrorBanner
          title="运行日志不存在或加载失败"
          description="该运行记录可能已被删除，或没有访问权限"
          onRetry={() => void mutate()}
        />
      </div>
    );
  }

  const status = detail.status ?? "";
  const variant = RUN_LOG_STATUS_VARIANT[status] ?? "info";
  const label = RUN_LOG_STATUS_LABEL[status] ?? detail.status ?? "未知";

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-8">
      <button
        type="button"
        onClick={() => router.replace(ROUTES.adminHome)}
        className="flex items-center gap-1 self-start text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
        返回管理端
      </button>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="text-2xl font-bold text-foreground">运行日志详情</h1>
          <code className="truncate text-xs text-muted-foreground">
            {detail.traceId}
          </code>
        </div>
        <Badge variant={variant}>{label}</Badge>
      </div>

      <TraceTimeline steps={detail.steps} />
    </div>
  );
}
