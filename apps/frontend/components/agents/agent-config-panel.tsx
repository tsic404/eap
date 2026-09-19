"use client";

import { RUN_LOG_STATUS_LABEL, RUN_LOG_STATUS_VARIANT } from "@/components/run-logs/run-log-labels";
import { Badge } from "@/components/ui/badge";
import { DataTable } from "@/components/ui/table";
import { Tabs } from "@/components/ui/tabs";
import type { AgentDetail, RunLog } from "@/lib/agent-types";
import { formatDateTime } from "@/lib/formatters";

import {
  AGENT_STATUS_LABEL,
  AGENT_STATUS_VARIANT,
  AGENT_TYPE_LABEL,
} from "./agent-labels";
import { MiniChatWindow } from "./mini-chat-window";

function DetailItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="text-sm text-foreground">{children}</div>
    </div>
  );
}

function ConfigTab({ agent }: { agent: AgentDetail }) {
  const statusLabel = AGENT_STATUS_LABEL[agent.status] ?? agent.status;
  const statusVariant = AGENT_STATUS_VARIANT[agent.status] ?? "default";

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <DetailItem label="标识">
        <code className="text-xs">{agent.agentId}</code>
      </DetailItem>
      <DetailItem label="状态">
        <Badge variant={statusVariant}>{statusLabel}</Badge>
      </DetailItem>
      <DetailItem label="名称">{agent.name}</DetailItem>
      <DetailItem label="类型">{AGENT_TYPE_LABEL[agent.type] ?? agent.type}</DetailItem>
      <DetailItem label="描述">{agent.description ?? "—"}</DetailItem>
      <DetailItem label="分类">{agent.category ?? "—"}</DetailItem>
      <DetailItem label="图标">{agent.icon ?? "🤖"}</DetailItem>
      <DetailItem label="模型">
        {[agent.modelProvider, agent.modelId].filter(Boolean).join(" / ") || "—"}
      </DetailItem>
      <DetailItem label="模型名称">{agent.modelName ?? "—"}</DetailItem>
      <DetailItem label="版本">{agent.version}</DetailItem>
      <div className="flex flex-col gap-1 sm:col-span-2">
        <span className="text-xs text-muted-foreground">标签</span>
        <div className="flex flex-wrap gap-1.5">
          {agent.tags.length === 0 ? (
            <span className="text-sm text-foreground">—</span>
          ) : (
            agent.tags.map((tag) => (
              <Badge key={tag} variant="default">
                {tag}
              </Badge>
            ))
          )}
        </div>
      </div>
      <div className="flex flex-col gap-1 sm:col-span-2">
        <span className="text-xs text-muted-foreground">系统 Prompt</span>
        <p className="whitespace-pre-wrap text-sm text-foreground">{agent.prompt ?? "—"}</p>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs text-muted-foreground">绑定知识库</span>
        <span className="text-sm text-foreground">
          {agent.boundKnowledge.length === 0
            ? "—"
            : agent.boundKnowledge.map((kb) => kb.name ?? kb.kbId).join("、")}
        </span>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs text-muted-foreground">绑定工具</span>
        <span className="text-sm text-foreground">
          {agent.boundTools.length === 0
            ? "—"
            : agent.boundTools.map((tool) => tool.name ?? tool.toolId).join("、")}
        </span>
      </div>
      <DetailItem label="创建时间">
        {agent.createdAt ? formatDateTime(agent.createdAt) : "—"}
      </DetailItem>
      <DetailItem label="更新时间">
        {agent.updatedAt ? formatDateTime(agent.updatedAt) : "—"}
      </DetailItem>
      <DetailItem label="发布时间">
        {agent.publishedAt ? formatDateTime(agent.publishedAt) : "—"}
      </DetailItem>
    </div>
  );
}

function LogsTab({ logs }: { logs: RunLog[] }) {
  return (
    <DataTable
      rowKey={(log) => log.traceId}
      data={logs}
      emptyTitle="暂无运行日志"
      emptyDescription="智能体运行后日志会展示在这里"
      pageSize={10}
      columns={[
        {
          key: "traceId",
          header: "Trace ID",
          cell: (log) => <code className="text-xs">{log.traceId}</code>,
        },
        {
          key: "status",
          header: "状态",
          cell: (log) => {
            const label = RUN_LOG_STATUS_LABEL[log.status ?? ""] ?? log.status ?? "未知";
            const variant = RUN_LOG_STATUS_VARIANT[log.status ?? ""] ?? "info";
            return <Badge variant={variant}>{label}</Badge>;
          },
        },
        {
          key: "input",
          header: "输入",
          cell: (log) => (
            <span className="block max-w-md truncate text-sm">{log.input ?? ""}</span>
          ),
        },
        {
          key: "latencyMs",
          header: "耗时",
          cell: (log) => (log.latencyMs !== null ? `${log.latencyMs} ms` : "—"),
        },
        {
          key: "createdAt",
          header: "时间",
          cell: (log) => (log.createdAt ? formatDateTime(log.createdAt) : "—"),
        },
      ]}
    />
  );
}

/** Detail-page panel: 配置 / 调试 / 日志 tabs for one agent. */
export function AgentConfigPanel({ agent }: { agent: AgentDetail }) {
  return (
    <Tabs
      items={[
        { value: "config", label: "配置", content: <ConfigTab agent={agent} /> },
        { value: "debug", label: "调试", content: <MiniChatWindow logs={agent.recentLogs} /> },
        { value: "logs", label: "日志", content: <LogsTab logs={agent.recentLogs} /> },
      ]}
    />
  );
}
