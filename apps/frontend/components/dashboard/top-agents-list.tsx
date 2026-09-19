"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { formatNumber } from "@/lib/formatters";
import type { TopAgent } from "@/lib/dashboard-types";

export interface TopAgentsListProps {
  agents: TopAgent[];
}

const RANK_BADGE_CLASS = [
  "bg-primary text-primary-foreground",
  "bg-primary-subtle text-primary",
  "bg-muted text-foreground",
] as const;

/** Ranked list of today's most-called agents. */
export function TopAgentsList({ agents }: TopAgentsListProps) {
  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <CardTitle>Top 智能体</CardTitle>
      </CardHeader>
      {agents.length === 0 ? (
        <CardContent>
          <EmptyState title="暂无排行" description="今日尚无智能体调用" />
        </CardContent>
      ) : (
        <CardContent className="flex flex-col gap-1">
          {agents.map((agent, index) => (
            <div
              key={agent.agentId}
              className="flex items-center gap-3 rounded-md px-2 py-1.5"
            >
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                  RANK_BADGE_CLASS[Math.min(index, RANK_BADGE_CLASS.length - 1)]
                }`}
              >
                {index + 1}
              </span>
              <span className="min-w-0 flex-1 truncate text-sm text-foreground">
                {agent.agentName ?? agent.agentId}
              </span>
              <span className="shrink-0 text-sm text-muted-foreground">
                {formatNumber(agent.calls)} 次
              </span>
            </div>
          ))}
        </CardContent>
      )}
    </Card>
  );
}
