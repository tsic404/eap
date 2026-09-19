"use client";

import Link from "next/link";
import { ListTodo } from "lucide-react";

import { AgentCard } from "@/components/agents/agent-card";
import { UserProfile } from "@/components/auth/user-profile";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { ROUTES } from "@/lib/api-routes";
import { formatDateTime, formatNumber } from "@/lib/formatters";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { useUserHome } from "@/lib/use-dashboard";

function UserHomeSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <Skeleton className="h-10 w-32" />
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-48 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

/** User home: pending-task count, recommended agents, recent conversations. */
export function UserHomePage() {
  const { data, error, isLoading, mutate } = useUserHome();

  if (isLoading) return <UserHomeSkeleton />;

  if (error) {
    return (
      <ErrorBanner
        description={extractApiErrorMessage(error)}
        onRetry={() => mutate()}
      />
    );
  }

  const home = data!;

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-bold text-foreground">用户工作区</h1>
        <p className="text-sm text-muted-foreground">
          推荐智能体、最近会话与待办任务
        </p>
      </header>

      <UserProfile />

      <Card>
        <CardContent className="flex items-center gap-3 p-4">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-subtle text-primary">
            <ListTodo className="h-5 w-5" />
          </span>
          <div className="flex flex-col">
            <span className="text-xs text-muted-foreground">待处理任务</span>
            <span className="text-lg font-semibold text-foreground">
              {formatNumber(home.pendingTaskCount)}
            </span>
          </div>
        </CardContent>
      </Card>

      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold text-foreground">推荐智能体</h2>
        {home.recommendedAgents.length === 0 ? (
          <EmptyState title="暂无推荐" description="发布智能体后将在此展示推荐" />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {home.recommendedAgents.map((agent) => (
              <AgentCard key={agent.agentId} agent={agent} />
            ))}
          </div>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold text-foreground">最近会话</h2>
        {home.recentConversations.length === 0 ? (
          <EmptyState title="暂无会话" description="开始对话后将在此展示最近会话" />
        ) : (
          <Card>
            <CardContent className="flex flex-col gap-1">
              {home.recentConversations.map((conversation) => (
                <Link
                  key={conversation.id}
                  href={ROUTES.conversationDetail(conversation.id)}
                  className="flex items-center gap-3 rounded-md px-2 py-2 transition-colors hover:bg-muted"
                >
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-sm font-medium text-foreground">
                      {conversation.title ?? "无标题会话"}
                    </span>
                    <span className="truncate text-xs text-muted-foreground">
                      {conversation.agentName ?? conversation.agentId}
                    </span>
                  </div>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatDateTime(conversation.updatedAt)}
                  </span>
                </Link>
              ))}
            </CardContent>
          </Card>
        )}
      </section>
    </div>
  );
}
