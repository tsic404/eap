"use client";

import { useId } from "react";
import { RotateCcw, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { Agent } from "@/lib/agent-types";
import {
  EMPTY_RUN_LOG_FILTERS,
  RUN_LOG_STATUSES,
  type RunLogFilters,
} from "@/lib/run-log-types";

import { RUN_LOG_STATUS_LABEL } from "./trace-labels";

export interface LogFilterBarProps {
  agents: Agent[];
  value: RunLogFilters;
  onChange: (next: RunLogFilters) => void;
}

const FIELD_CLASS =
  "h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

const LABEL_CLASS = "text-sm font-medium text-foreground";

/** Filter bar for the run-log list: agent / user / status / time range. */
export function LogFilterBar({
  agents,
  value,
  onChange,
}: LogFilterBarProps) {
  const id = useId();

  const hasActiveFilters =
    value.agentId !== "" ||
    value.conversationId !== "" ||
    value.status !== "" ||
    value.from !== "" ||
    value.to !== "";

  const set = (patch: Partial<RunLogFilters>) =>
    onChange({ ...value, ...patch });

  return (
    <Card>
      <CardContent className="flex flex-col gap-4 p-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <div className="flex flex-col gap-1.5">
            <label htmlFor={`${id}-agent`} className={LABEL_CLASS}>
              智能体
            </label>
            <select
              id={`${id}-agent`}
              value={value.agentId}
              onChange={(event) => set({ agentId: event.target.value })}
              className={FIELD_CLASS}
            >
              <option value="">全部智能体</option>
              {agents.map((agent) => (
                <option key={agent.agentId} value={agent.agentId}>
                  {agent.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={`${id}-conversation`} className={LABEL_CLASS}>
              会话
            </label>
            <input
              id={`${id}-conversation`}
              type="text"
              value={value.conversationId}
              onChange={(event) => set({ conversationId: event.target.value })}
              placeholder="按会话 ID 筛选"
              className={FIELD_CLASS}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={`${id}-status`} className={LABEL_CLASS}>
              状态
            </label>
            <select
              id={`${id}-status`}
              value={value.status}
              onChange={(event) =>
                set({ status: event.target.value as RunLogFilters["status"] })
              }
              className={FIELD_CLASS}
            >
              <option value="">全部状态</option>
              {RUN_LOG_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {RUN_LOG_STATUS_LABEL[status]}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={`${id}-from`} className={LABEL_CLASS}>
              开始日期
            </label>
            <input
              id={`${id}-from`}
              type="date"
              value={value.from}
              onChange={(event) => set({ from: event.target.value })}
              className={FIELD_CLASS}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={`${id}-to`} className={LABEL_CLASS}>
              结束日期
            </label>
            <input
              id={`${id}-to`}
              type="date"
              value={value.to}
              onChange={(event) => set({ to: event.target.value })}
              className={FIELD_CLASS}
            />
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">
            {hasActiveFilters ? "已应用筛选条件" : "显示全部运行日志"}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={!hasActiveFilters}
            onClick={() => onChange(EMPTY_RUN_LOG_FILTERS)}
          >
            {hasActiveFilters ? (
              <RotateCcw className="h-4 w-4" />
            ) : (
              <Search className="h-4 w-4" />
            )}
            重置
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
