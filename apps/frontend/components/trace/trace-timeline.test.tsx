import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TraceStep } from "@/lib/run-log-types";

import { renderWithIntl } from "../test-utils";
import { TraceTimeline } from "./trace-timeline";

function step(overrides: Partial<TraceStep> = {}): TraceStep {
  return {
    stepOrder: 0,
    name: "步骤",
    type: null,
    status: null,
    latencyMs: null,
    detail: null,
    ...overrides,
  };
}

describe("TraceTimeline", () => {
  it("renders an empty state when there are no steps", () => {
    renderWithIntl(<TraceTimeline steps={[]} />);

    expect(screen.getByText("暂无步骤")).toBeTruthy();
  });

  it("renders numbered steps with name, status, duration and detail", () => {
    renderWithIntl(
      <TraceTimeline
        steps={[
          step({ stepOrder: 0, name: "检索知识库", type: "retrieval", status: "success", latencyMs: 120, detail: "命中 3 条" }),
          step({ stepOrder: 1, name: "生成回复", status: "running" }),
        ]}
      />,
    );

    expect(screen.getByText("检索知识库")).toBeTruthy();
    expect(screen.getByText("生成回复")).toBeTruthy();
    // Circles render `stepOrder + 1`.
    expect(screen.getByText("1")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.getByText("retrieval")).toBeTruthy();
    expect(screen.getByText("120 ms")).toBeTruthy();
    expect(screen.getByText("命中 3 条")).toBeTruthy();
    // Missing duration renders a placeholder rather than a blank gap.
    expect(screen.getByText("—")).toBeTruthy();
  });

  it("numbers circles from the step's own stepOrder, not render position", () => {
    renderWithIntl(
      <TraceTimeline
        steps={[
          step({ stepOrder: 4, name: "a" }),
          step({ stepOrder: 2, name: "b" }),
        ]}
      />,
    );

    expect(screen.getByText("5")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
  });

  it("animates running (spin) and blocked (pulse) steps, leaving static steps still", () => {
    renderWithIntl(
      <TraceTimeline
        steps={[
          step({ stepOrder: 0, name: "a", status: "running" }),
          step({ stepOrder: 1, name: "b", status: "success" }),
          step({ stepOrder: 2, name: "c", status: "blocked" }),
        ]}
      />,
    );

    // Running step: a decorative ring spins while the numbered circle stays still.
    expect(screen.getByText("1").parentElement?.querySelector(".animate-spin")).toBeTruthy();
    expect(screen.getByText("1").className).not.toContain("animate-spin");
    // Blocked step: the numbered circle itself pulses.
    expect(screen.getByText("3").className).toContain("animate-pulse");
    // Static (success) step: no animation on or around its circle.
    expect(screen.getByText("2").className).not.toContain("animate-spin");
    expect(screen.getByText("2").className).not.toContain("animate-pulse");
    expect(screen.getByText("2").parentElement?.querySelector(".animate-spin")).toBeNull();
  });

  it("marks only the highest-order running step as the current one", () => {
    const { container } = renderWithIntl(
      <TraceTimeline
        steps={[
          step({ stepOrder: 5, name: "a", status: "running" }),
          step({ stepOrder: 9, name: "b", status: "running" }),
          step({ stepOrder: 7, name: "c", status: "running" }),
        ]}
      />,
    );

    // Exactly one pulse ring, on the highest stepOrder (9 → "10").
    expect(container.querySelectorAll(".animate-ping")).toHaveLength(1);
    expect(screen.getByText("10").parentElement?.querySelector(".animate-ping")).toBeTruthy();
    expect(screen.getByText("6").parentElement?.querySelector(".animate-ping")).toBeNull();
    expect(screen.getByText("8").parentElement?.querySelector(".animate-ping")).toBeNull();
  });

  it("picks a single current step when stepOrders tie", () => {
    const { container } = renderWithIntl(
      <TraceTimeline
        steps={[
          step({ stepOrder: 0, name: "a", status: "running" }),
          step({ stepOrder: 0, name: "b", status: "running" }),
          step({ stepOrder: 0, name: "c", status: "running" }),
        ]}
      />,
    );

    // All stepOrders tie at 0, yet exactly one current ring renders.
    expect(container.querySelectorAll(".animate-ping")).toHaveLength(1);
    // The latest running step (last list item) wins the tie.
    const rows = container.querySelectorAll("li");
    expect(rows[0].querySelector(".animate-ping")).toBeNull();
    expect(rows[1].querySelector(".animate-ping")).toBeNull();
    expect(rows[2].querySelector(".animate-ping")).toBeTruthy();
  });

  it("maps each status to the shared badge variant and falls back for unknown/absent", () => {
    renderWithIntl(
      <TraceTimeline
        steps={[
          step({ name: "a", status: "success" }),
          step({ name: "b", status: "failed" }),
          step({ name: "c", status: "running" }),
          step({ name: "d", status: "blocked" }),
          step({ name: "e", status: null }),
          step({ name: "f", status: "weird" }),
        ]}
      />,
    );

    expect(screen.getByText("成功").className).toContain("text-success");
    expect(screen.getByText("失败").className).toContain("text-danger");
    expect(screen.getByText("运行中").className).toContain("text-info");
    expect(screen.getByText("已阻塞").className).toContain("text-warning");
    // Absent status → "未知" label with the info fallback variant.
    expect(screen.getByText("未知").className).toContain("text-info");
    // Unknown status → raw value with the info fallback variant.
    expect(screen.getByText("weird").className).toContain("text-info");
  });
});
