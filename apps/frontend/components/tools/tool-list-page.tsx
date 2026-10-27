"use client";

import { useRouter } from "next/navigation";
import { Plus, Wrench } from "lucide-react";

import { useRole } from "@/components/auth/role-context";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { ROUTES } from "@/lib/api-routes";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { canManageTools } from "@/lib/roles";
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
  const role = useRole();
  const canCreate = canManageTools(role);
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
        {canCreate && (
          <Button onClick={() => router.push(ROUTES.adminToolsNew)}>
            <Plus className="h-4 w-4" />
            新建工具
          </Button>
        )}
      </div>

      {tools.length === 0 ? (
        <EmptyState
          variant="card"
          icon={<Wrench className="h-6 w-6" />}
          title="创建第一个工具"
          description="工具描述可被智能体调用的外部 API；注册后配置鉴权，即可在调试面板试请求。"
          action={
            canCreate ? (
              <Button onClick={() => router.push(ROUTES.adminToolsNew)}>
                新建工具
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div
          role="list"
          aria-label="工具列表"
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
        >
          {tools.map((tool) => (
            <div key={tool.tool_id} role="listitem">
              <ToolCard
                tool={tool}
                onClick={() => router.push(ROUTES.adminToolDetail(tool.tool_id))}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
