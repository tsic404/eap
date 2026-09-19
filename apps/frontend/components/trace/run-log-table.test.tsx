import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RunLogSummary } from "@/lib/run-log-types";

import { RunLogTable } from "./run-log-table";

function makeRow(overrides: Partial<RunLogSummary> = {}): RunLogSummary {
  return {
    traceId: "trace-1",
    conversationId: "conv-1",
    agentId: "agent-1",
    agentName: "客服助手",
    userId: "user-1",
    userName: "张三",
    status: "success",
    input: "你好",
    modelName: "gpt-4",
    tokenUsage: 100,
    latencyMs: 1500,
    toolCallCount: 1,
    knowledgeHitCount: 2,
    createdAt: "2026-01-15T10:00:00Z",
    ...overrides,
  };
}

const ROWS: RunLogSummary[] = [
  makeRow(),
  makeRow({ traceId: "trace-2", status: "failed", agentName: "数据助手" }),
];

describe("RunLogTable", () => {
  it("renders rows with human status labels", () => {
    render(<RunLogTable items={ROWS} onSelect={() => {}} />);

    expect(screen.getByText("成功")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.getByText("客服助手")).toBeTruthy();
    expect(screen.getByText("数据助手")).toBeTruthy();
  });

  it("navigates to the trace detail when a row is clicked", () => {
    const onSelect = vi.fn();
    render(<RunLogTable items={ROWS} onSelect={onSelect} />);

    fireEvent.click(screen.getByText("客服助手"));

    expect(onSelect).toHaveBeenCalledWith("trace-1");
  });
});
