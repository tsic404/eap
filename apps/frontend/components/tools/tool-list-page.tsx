"use client";

import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { ROUTES } from "@/lib/api-routes";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { useTools } from "@/lib/use-tools";

import { ToolCard } from "./tool-card";

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }, (_, index) => (
        <Skeleton key={index} className="h-40 w-full" />
      ))}
    </div>
  );
}

/** Admin tool listing with risk-level badges on each card. */
export function ToolListPage() {
  const router = useRouter();
  const { data, error, isLoading, mutate } = useTools();

  if (isLoading) {
    return (
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-8">
        <Skeleton className="h-10 w-48" />
        <SkeletonGrid />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-6xl px-6 py-8">
        <ErrorBanner
          description={extractApiErrorMessage(error)}
          onRetry={() => mutate()}
        />
      </div>
    );
  }

  const tools = data ?? [];

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">工具</h1>
          <p className="text-sm text-muted-foreground">注册与调试外部 API 工具</p>
        </div>
        <Button onClick={() => router.push(ROUTES.adminToolsNew)}>
          <Plus className="h-4 w-4" />
          新建工具
        </Button>
      </div>

      {tools.length === 0 ? (
        <EmptyState
          title="暂无工具"
          description="注册第一个工具以开始"
          action={
            <Button onClick={() => router.push(ROUTES.adminToolsNew)}>
              新建工具
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {tools.map((tool) => (
            <ToolCard
              key={tool.tool_id}
              tool={tool}
              onClick={() => router.push(ROUTES.adminToolDetail(tool.tool_id))}
            />
          ))}
        </div>
      )}
    </div>
  );
}
