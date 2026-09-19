"use client";

import { Badge } from "@/components/ui/badge";
import type { Column } from "@/components/ui/table";
import { DataTable } from "@/components/ui/table";
import { formatDateTime, formatDuration, formatNumber } from "@/lib/formatters";
import type { RunLogSummary } from "@/lib/run-log-types";

import { runLogStatusLabel, runLogStatusVariant } from "./trace-labels";

export interface RunLogTableProps {
  items: RunLogSummary[];
  onSelect: (traceId: string) => void;
}

const COLUMNS: Column<RunLogSummary>[] = [
  {
    key: "status",
    header: "状态",
    cell: (row) => (
      <Badge variant={runLogStatusVariant(row.status)}>
        {runLogStatusLabel(row.status)}
      </Badge>
    ),
  },
  {
    key: "agentName",
    header: "智能体",
    cell: (row) => row.agentName ?? "—",
  },
  {
    key: "userName",
    header: "用户",
    cell: (row) => row.userName ?? "—",
  },
  {
    key: "input",
    header: "输入",
    cell: (row) =>
      row.input ? (
        <span className="block max-w-56 truncate" title={row.input}>
          {row.input}
        </span>
      ) : (
        "—"
      ),
  },
  {
    key: "tokenUsage",
    header: "Token",
    cell: (row) =>
      row.tokenUsage == null ? "—" : formatNumber(row.tokenUsage),
  },
  {
    key: "latencyMs",
    header: "耗时",
    cell: (row) => formatDuration(row.latencyMs),
  },
  {
    key: "createdAt",
    header: "时间",
    cell: (row) => formatDateTime(row.createdAt),
  },
];

/** Paginated run-log table; clicking a row opens its trace detail. */
export function RunLogTable({ items, onSelect }: RunLogTableProps) {
  return (
    <DataTable
      columns={COLUMNS}
      data={items}
      rowKey={(row) => row.traceId}
      onRowClick={(row) => onSelect(row.traceId)}
      pageSize={10}
      emptyTitle="暂无运行日志"
      emptyDescription="暂无符合条件的运行记录"
    />
  );
}
