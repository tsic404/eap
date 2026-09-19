import { describe, expect, it } from "vitest";

import {
  evaluateDashboardHealth,
  formatPercent,
} from "./dashboard-health";
import type { MetricSummary } from "./dashboard-types";

function metrics(overrides: Partial<MetricSummary> = {}): MetricSummary {
  return {
    totalAgents: 10,
    todayCalls: 100,
    avgLatencyMs: 500,
    errorRate: 0,
    ...overrides,
  };
}

describe("evaluateDashboardHealth", () => {
  it("reports healthy when both signals are within bounds", () => {
    const result = evaluateDashboardHealth(metrics());
    expect(result.level).toBe("healthy");
    expect(result.alerts).toEqual([]);
  });

  it("reports degraded at the error-rate warning threshold", () => {
    const result = evaluateDashboardHealth(metrics({ errorRate: 0.02 }));
    expect(result.level).toBe("degraded");
    expect(result.alerts.map((a) => a.id)).toEqual(["error-rate"]);
    expect(result.alerts[0].severity).toBe("warning");
  });

  it("reports critical at the error-rate critical threshold", () => {
    const result = evaluateDashboardHealth(metrics({ errorRate: 0.1 }));
    expect(result.level).toBe("critical");
    expect(result.alerts[0].severity).toBe("danger");
  });

  it("reports degraded at the latency warning threshold", () => {
    const result = evaluateDashboardHealth(metrics({ avgLatencyMs: 2_000 }));
    expect(result.level).toBe("degraded");
    expect(result.alerts.map((a) => a.id)).toEqual(["latency"]);
  });

  it("reports critical at the latency critical threshold", () => {
    const result = evaluateDashboardHealth(metrics({ avgLatencyMs: 10_000 }));
    expect(result.level).toBe("critical");
    expect(result.alerts[0].severity).toBe("danger");
  });

  it("treats a missing latency as a healthy signal", () => {
    const result = evaluateDashboardHealth(
      metrics({ avgLatencyMs: null, errorRate: 0.5 }),
    );
    expect(result.level).toBe("critical");
    expect(result.checks[1].status).toBe("ok");
    expect(result.alerts.map((a) => a.id)).toEqual(["error-rate"]);
  });

  it("succeeds critical over degraded when a single signal is critical", () => {
    const result = evaluateDashboardHealth(
      metrics({ errorRate: 0.02, avgLatencyMs: 10_000 }),
    );
    expect(result.level).toBe("critical");
  });
});

describe("formatPercent", () => {
  it("formats a fraction with one decimal place", () => {
    expect(formatPercent(0)).toBe("0.0%");
    expect(formatPercent(0.05)).toBe("5.0%");
    expect(formatPercent(0.123)).toBe("12.3%");
  });

  it("honors an explicit digit count", () => {
    expect(formatPercent(0.1, 0)).toBe("10%");
  });
});
