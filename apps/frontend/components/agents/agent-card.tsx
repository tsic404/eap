"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Agent } from "@/lib/agent-types";

import { AGENT_STATUS_LABEL, AGENT_STATUS_VARIANT, AGENT_TYPE_LABEL } from "./agent-labels";

const MAX_VISIBLE_TAGS = 4;

export interface AgentCardProps {
  agent: Agent;
  onClick?: () => void;
}

/** Marketplace card: icon + name + status + description + tags + type/category. */
export function AgentCard({ agent, onClick }: AgentCardProps) {
  const statusLabel = AGENT_STATUS_LABEL[agent.status] ?? agent.status;
  const statusVariant = AGENT_STATUS_VARIANT[agent.status] ?? "default";
  const typeLabel = AGENT_TYPE_LABEL[agent.type] ?? agent.type;
  const visibleTags = agent.tags.slice(0, MAX_VISIBLE_TAGS);
  const remainingTags = agent.tags.length - visibleTags.length;

  return (
    <Card
      variant={onClick ? "interactive" : "default"}
      onClick={onClick}
      className="flex h-full flex-col"
    >
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-muted text-2xl leading-none">
              {agent.icon ?? "🤖"}
            </span>
            <div className="flex min-w-0 flex-col">
              <CardTitle className="truncate">{agent.name}</CardTitle>
              <span className="text-xs text-muted-foreground">{typeLabel}</span>
            </div>
          </div>
          <Badge variant={statusVariant}>{statusLabel}</Badge>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col justify-between gap-3">
        <p className="line-clamp-2 text-sm text-muted-foreground">
          {agent.description ?? "暂无描述"}
        </p>
        <div className="flex flex-col gap-2">
          {visibleTags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {visibleTags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
                >
                  {tag}
                </span>
              ))}
              {remainingTags > 0 && (
                <span className="text-xs text-muted-foreground">+{remainingTags}</span>
              )}
            </div>
          )}
          {agent.category && (
            <span className="text-xs text-muted-foreground">{agent.category}</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
