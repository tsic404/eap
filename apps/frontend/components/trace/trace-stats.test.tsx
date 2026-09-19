import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TraceStats } from "./trace-stats";

describe("TraceStats", () => {
  it("renders all four metrics with formatted values", () => {
    render(
      <TraceStats
        tokenUsage={1234}
        latencyMs={1500}
        toolCallCount={3}
        knowledgeHitCount={5}
      />,
    );

    expect(screen.getByText("Token 用量")).toBeTruthy();
    expect(screen.getByText("1,234")).toBeTruthy();
    expect(screen.getByText("总延迟")).toBeTruthy();
    expect(screen.getByText("1.5 s")).toBeTruthy();
    expect(screen.getByText("工具调用次数")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("知识命中数")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();
  });

  it("renders an em dash for a missing token count", () => {
    render(
      <TraceStats
        tokenUsage={null}
        latencyMs={null}
        toolCallCount={0}
        knowledgeHitCount={0}
      />,
    );

    expect(screen.getAllByText("—")).toHaveLength(2);
  });
});
