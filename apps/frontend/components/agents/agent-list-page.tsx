"use client";

import { useRouter } from "next/navigation";
import { Bot, Plus } from "lucide-react";

import { useRole } from "@/components/auth/role-context";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { ROUTES } from "@/lib/api-routes";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { canManageAgents } from "@/lib/roles";
import { useAllAgents } from "@/lib/use-agents";

import { AgentCard } from "./agent-card";

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }, (_, index) => (
        <Skeleton key={index} className="h-40 w-full" />
      ))}
    </div>
  );
}

/**
 * Admin agent listing. Read-only roles reach it without a create entry; an
 * empty tenant sees the create-first guidance card instead of a blank grid.
 */
export function AgentListPage() {
  const router = useRouter();
  const role = useRole();
  const canCreate = canManageAgents(role);
  const { data, error, isLoading, mutate } = useAllAgents();

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

  const agents = data ?? [];

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">智能体</h1>
          <p className="text-sm text-muted-foreground">管理与发布智能体资产</p>
        </div>
        {canCreate && (
          <Button onClick={() => router.push(ROUTES.adminAgentsNew)}>
            <Plus className="h-4 w-4" />
            新建智能体
          </Button>
        )}
      </div>

      {agents.length === 0 ? (
        <EmptyState
          variant="card"
          icon={<Bot className="h-6 w-6" />}
          title="创建第一个智能体"
          description="智能体创建后可绑定知识库与工具、调试并发布给员工使用。"
          action={
            canCreate ? (
              <Button onClick={() => router.push(ROUTES.adminAgentsNew)}>
                新建智能体
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div
          role="list"
          aria-label="智能体列表"
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
        >
          {agents.map((agent) => (
            <div key={agent.agentId} role="listitem">
              <AgentCard
                agent={agent}
                href={ROUTES.adminAgentDetail(agent.agentId)}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
