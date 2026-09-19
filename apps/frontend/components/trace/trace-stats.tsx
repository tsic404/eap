"use client";

import { Clock, Coins, Cpu, Database } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { formatDuration, formatNumber } from "@/lib/formatters";

export interface TraceStatsProps {
  tokenUsage: number | null;
  latencyMs: number | null;
  toolCallCount: number;
  knowledgeHitCount: number;
}

const STAT_ICON_CLASS = "h-5 w-5";

/** Four-metric summary strip for a trace: token/latency/tool/knowledge. */
export function TraceStats({
  tokenUsage,
  latencyMs,
  toolCallCount,
  knowledgeHitCount,
}: TraceStatsProps) {
  const stats = [
    {
      key: "tokens",
      label: "Token 用量",
      icon: Coins,
      value: tokenUsage == null ? "—" : formatNumber(tokenUsage),
    },
    {
      key: "latency",
      label: "总延迟",
      icon: Clock,
      value: formatDuration(latencyMs),
    },
    {
      key: "toolCalls",
      label: "工具调用次数",
      icon: Cpu,
      value: formatNumber(toolCallCount),
    },
    {
      key: "knowledge",
      label: "知识命中数",
      icon: Database,
      value: formatNumber(knowledgeHitCount),
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {stats.map(({ key, label, icon: Icon, value }) => (
        <Card key={key}>
          <CardContent className="flex items-center gap-3 p-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-subtle text-primary">
              <Icon className={STAT_ICON_CLASS} />
            </div>
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-xs text-muted-foreground">
                {label}
              </span>
              <span className="text-lg font-semibold text-foreground">
                {value}
              </span>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
