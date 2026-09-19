"use client";

import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs } from "@/components/ui/tabs";
import { ROUTES } from "@/lib/api-routes";
import { formatDateTime, formatDuration } from "@/lib/formatters";
import { extractApiErrorMessage } from "@/lib/platform-service";
import type { TraceCitation, TraceToolCall } from "@/lib/run-log-types";
import { useRunLog } from "@/lib/use-run-logs";

import { TraceStats } from "./trace-stats";
import { TraceTimeline } from "./trace-timeline";
import { runLogStatusLabel, runLogStatusVariant } from "./trace-labels";

function DetailSkeleton() {
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 px-6 py-8">
      <Skeleton className="h-10 w-64" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

function CitationsSection({ citations }: { citations: TraceCitation[] }) {
  if (citations.length === 0) {
    return <EmptyState title="暂无知识引用" />;
  }
  return (
    <ol className="flex flex-col gap-3">
      {citations.map((citation, index) => (
        <li
          key={index}
          className="flex flex-col gap-1 rounded-md border border-border p-3"
        >
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium text-foreground">
              {citation.sourceName ?? "未知来源"}
            </span>
            {citation.kbName && (
              <span className="text-xs text-muted-foreground">
                {citation.kbName}
              </span>
            )}
            {citation.score != null && (
              <span className="text-xs text-muted-foreground">
                相关度 {citation.score.toFixed(3)}
              </span>
            )}
          </div>
          {citation.excerpt && (
            <p className="text-sm text-muted-foreground">{citation.excerpt}</p>
          )}
        </li>
      ))}
    </ol>
  );
}

function ToolCallsSection({ toolCalls }: { toolCalls: TraceToolCall[] }) {
  if (toolCalls.length === 0) {
    return <EmptyState title="暂无工具调用" />;
  }
  return (
    <ol className="flex flex-col gap-3">
      {toolCalls.map((call, index) => (
        <li
          key={index}
          className="flex flex-col gap-1 rounded-md border border-border p-3"
        >
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium text-foreground">
              {call.toolName ?? "未知工具"}
            </span>
            {call.status && (
              <Badge variant={runLogStatusVariant(call.status)}>
                {runLogStatusLabel(call.status)}
              </Badge>
            )}
            {call.latencyMs != null && (
              <span className="text-xs text-muted-foreground">
                {formatDuration(call.latencyMs)}
              </span>
            )}
          </div>
          {call.requestSummary && (
            <p className="text-sm text-muted-foreground">
              请求：{call.requestSummary}
            </p>
          )}
          {call.responseSummary && (
            <p className="text-sm text-muted-foreground">
              响应：{call.responseSummary}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}

/** Admin run-log detail: metrics + overview + steps/citations/tool calls. */
export function RunLogDetailPage() {
  const params = useParams<{ traceId: string }>();
  const traceId = params.traceId;
  const router = useRouter();
  const { data: log, error, isLoading } = useRunLog(traceId);

  if (isLoading) {
    return <DetailSkeleton />;
  }

  if (error) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-8">
        <Button
          variant="ghost"
          size="sm"
          className="mb-4"
          onClick={() => router.push(ROUTES.adminRunLogs)}
        >
          <ArrowLeft className="h-4 w-4" />
          返回
        </Button>
        <ErrorBanner description={extractApiErrorMessage(error)} />
      </div>
    );
  }

  if (!log) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-8">
        <EmptyState title="未找到运行日志" />
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 px-6 py-8">
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push(ROUTES.adminRunLogs)}
        >
          <ArrowLeft className="h-4 w-4" />
          返回
        </Button>
        <div className="flex min-w-0 flex-col">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-foreground">运行日志详情</h1>
            <Badge variant={runLogStatusVariant(log.status)}>
              {runLogStatusLabel(log.status)}
            </Badge>
          </div>
          <p className="truncate text-sm text-muted-foreground" title={traceId}>
            {traceId}
          </p>
        </div>
      </div>

      <TraceStats
        tokenUsage={log.tokenUsage}
        latencyMs={log.latencyMs}
        toolCallCount={log.toolCallCount}
        knowledgeHitCount={log.knowledgeHitCount}
      />

      <Card>
        <CardHeader>
          <CardTitle>概要</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-muted-foreground">智能体</dt>
              <dd className="text-sm text-foreground">{log.agentName ?? "—"}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-muted-foreground">用户</dt>
              <dd className="text-sm text-foreground">{log.userName ?? "—"}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-muted-foreground">模型</dt>
              <dd className="text-sm text-foreground">{log.modelName ?? "—"}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-xs text-muted-foreground">时间</dt>
              <dd className="text-sm text-foreground">
                {formatDateTime(log.createdAt)}
              </dd>
            </div>
          </dl>
          {log.input && (
            <div className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">输入</span>
              <p className="whitespace-pre-wrap text-sm text-foreground">
                {log.input}
              </p>
            </div>
          )}
          {log.output && (
            <div className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">输出</span>
              <p className="whitespace-pre-wrap text-sm text-foreground">
                {log.output}
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      <Tabs
        defaultValue="steps"
        items={[
          {
            value: "steps",
            label: `步骤 (${log.steps.length})`,
            content: <TraceTimeline steps={log.steps} />,
          },
          {
            value: "citations",
            label: `知识引用 (${log.citations.length})`,
            content: <CitationsSection citations={log.citations} />,
          },
          {
            value: "toolCalls",
            label: `工具调用 (${log.toolCalls.length})`,
            content: <ToolCallsSection toolCalls={log.toolCalls} />,
          },
        ]}
      />
    </div>
  );
}
