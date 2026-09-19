import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TopAgent } from "@/lib/dashboard-types";

import { TopAgentsList } from "./top-agents-list";

const AGENTS: TopAgent[] = [
  { agentId: "agent-a", agentName: "客服助手", calls: 120 },
  { agentId: "agent-b", agentName: null, calls: 80 },
  { agentId: "agent-c", agentName: "数据分析", calls: 5 },
];

describe("TopAgentsList", () => {
  it("renders agents in call-count order with rank badges", () => {
    render(<TopAgentsList agents={AGENTS} />);

    expect(screen.getByText("客服助手")).toBeTruthy();
    // A null name falls back to the raw agent id.
    expect(screen.getByText("agent-b")).toBeTruthy();
    expect(screen.getByText("数据分析")).toBeTruthy();
    expect(screen.getByText("120 次")).toBeTruthy();
  });

  it("renders an empty state when there are no agents", () => {
    render(<TopAgentsList agents={[]} />);

    expect(screen.getByText("暂无排行")).toBeTruthy();
  });
});
