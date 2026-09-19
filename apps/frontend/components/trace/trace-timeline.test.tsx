import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TraceStep } from "@/lib/run-log-types";

import { TraceTimeline } from "./trace-timeline";

const STEPS: TraceStep[] = [
  {
    stepOrder: 0,
    name: "检索知识库",
    type: "retrieval",
    status: "success",
    latencyMs: 250,
    detail: "命中 2 条",
  },
  {
    stepOrder: 1,
    name: "调用工具",
    type: "tool",
    status: "failed",
    latencyMs: 1200,
    detail: null,
  },
  {
    stepOrder: 2,
    name: "生成回复",
    type: "llm",
    status: null,
    latencyMs: null,
    detail: null,
  },
];

describe("TraceTimeline", () => {
  it("renders numbered steps with names and latencies", () => {
    render(<TraceTimeline steps={STEPS} />);

    expect(screen.getByText("1")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("检索知识库")).toBeTruthy();
    expect(screen.getByText("250 ms")).toBeTruthy();
    expect(screen.getByText("1.2 s")).toBeTruthy();
    expect(screen.getByText("命中 2 条")).toBeTruthy();
  });

  it("colors step circles by status", () => {
    render(<TraceTimeline steps={STEPS} />);

    expect(screen.getByText("1").className).toContain("text-success");
    expect(screen.getByText("2").className).toContain("text-danger");
    expect(screen.getByText("3").className).toContain("text-muted-foreground");
  });

  it("renders status labels for known and unknown statuses", () => {
    render(<TraceTimeline steps={STEPS} />);

    expect(screen.getByText("成功")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.getByText("未知")).toBeTruthy();
  });
});
