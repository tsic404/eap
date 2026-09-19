import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Agent } from "@/lib/agent-types";
import { EMPTY_RUN_LOG_FILTERS } from "@/lib/run-log-types";

import { LogFilterBar } from "./log-filter-bar";

function makeAgent(agentId: string, name: string): Agent {
  return {
    agentId,
    name,
    tenantId: "tenant-1",
    difyAppId: "app-1",
    description: null,
    type: "chat",
    category: null,
    icon: null,
    tags: [],
    status: "published",
    modelId: null,
    modelName: null,
    modelProvider: null,
    prompt: null,
    version: 1,
    createdBy: null,
    publishedAt: null,
    publishedBy: null,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
  };
}

describe("LogFilterBar", () => {
  it("renders the four filter dimensions", () => {
    render(
      <LogFilterBar
        agents={[makeAgent("a1", "客服助手")]}
        value={EMPTY_RUN_LOG_FILTERS}
        onChange={() => {}}
      />,
    );

    expect(screen.getByText("智能体")).toBeTruthy();
    expect(screen.getByText("会话")).toBeTruthy();
    expect(screen.getByText("状态")).toBeTruthy();
    expect(screen.getByText("开始日期")).toBeTruthy();
    expect(screen.getByText("结束日期")).toBeTruthy();
  });

  it("emits the next filter value when a dimension changes", () => {
    const onChange = vi.fn();
    render(
      <LogFilterBar
        agents={[makeAgent("a1", "客服助手")]}
        value={EMPTY_RUN_LOG_FILTERS}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByLabelText("状态"), {
      target: { value: "failed" },
    });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ status: "failed" }),
    );

    fireEvent.change(screen.getByLabelText("会话"), {
      target: { value: "alice" },
    });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ conversationId: "alice" }),
    );
  });

  it("disables reset until a filter is active", () => {
    render(
      <LogFilterBar
        agents={[]}
        value={EMPTY_RUN_LOG_FILTERS}
        onChange={() => {}}
      />,
    );

    const resetButton = screen.getByRole("button", {
      name: "重置",
    }) as HTMLButtonElement;
    expect(resetButton.disabled).toBeTruthy();
  });
});
