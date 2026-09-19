/**
 * Pure derivation of the admin dashboard's "system health" view.
 *
 * The backend deliverable has no dedicated health/alert endpoint, so health
 * and alerts are derived from the two signals `GET /api/dashboard/admin`
 * already returns — `errorRate` and `avgLatencyMs` — per the issue background
 * ("系统健康状态"). Thresholds are named constants so they can be tuned
 * without touching the UI.
 */

import type { MetricSummary } from "./dashboard-types";
import { formatDuration } from "./formatters";

export type HealthLevel = "healthy" | "degraded" | "critical";
export type CheckStatus = "ok" | "warning" | "danger";
export type AlertSeverity = "warning" | "danger";

export interface HealthCheck {
  label: string;
  status: CheckStatus;
  detail: string;
}

export interface DashboardAlert {
  id: string;
  severity: AlertSeverity;
  title: string;
  description: string;
}

export interface DashboardHealth {
  level: HealthLevel;
  checks: HealthCheck[];
  alerts: DashboardAlert[];
}

export const ERROR_RATE_WARNING = 0.02;
export const ERROR_RATE_CRITICAL = 0.1;
export const LATENCY_WARNING_MS = 2_000;
export const LATENCY_CRITICAL_MS = 10_000;

export const HEALTH_LEVEL_LABEL: Record<HealthLevel, string> = {
  healthy: "健康",
  degraded: "降级",
  critical: "异常",
};

/** Format a `[0, 1]` fraction as a percentage with one decimal place. */
export function formatPercent(fraction: number, digits = 1): string {
  return `${(fraction * 100).toFixed(digits)}%`;
}

/** Map a measured value to a three-tier status against warning/critical bounds. */
function signalStatus(
  value: number,
  warning: number,
  critical: number,
): CheckStatus {
  if (value >= critical) return "danger";
  if (value >= warning) return "warning";
  return "ok";
}

/**
 * Evaluate the admin metrics into a health level, per-signal checks, and the
 * alerts to surface. One call site keeps the tiering logic in a single place.
 */
export function evaluateDashboardHealth(metrics: MetricSummary): DashboardHealth {
  const errorRateStatus = signalStatus(
    metrics.errorRate,
    ERROR_RATE_WARNING,
    ERROR_RATE_CRITICAL,
  );
  const latencyStatus =
    metrics.avgLatencyMs == null
      ? "ok"
      : signalStatus(metrics.avgLatencyMs, LATENCY_WARNING_MS, LATENCY_CRITICAL_MS);

  const level: HealthLevel =
    errorRateStatus === "danger" || latencyStatus === "danger"
      ? "critical"
      : errorRateStatus === "warning" || latencyStatus === "warning"
        ? "degraded"
        : "healthy";

  const checks: HealthCheck[] = [
    {
      label: "错误率",
      status: errorRateStatus,
      detail: formatPercent(metrics.errorRate),
    },
    {
      label: "平均耗时",
      status: latencyStatus,
      detail: formatDuration(metrics.avgLatencyMs),
    },
  ];

  const alerts: DashboardAlert[] = [];
  if (errorRateStatus !== "ok") {
    alerts.push({
      id: "error-rate",
      severity: errorRateStatus === "danger" ? "danger" : "warning",
      title: errorRateStatus === "danger" ? "错误率过高" : "错误率偏高",
      description: `今日错误率 ${formatPercent(metrics.errorRate)}，已超过 ${formatPercent(
        errorRateStatus === "danger" ? ERROR_RATE_CRITICAL : ERROR_RATE_WARNING,
        0,
      )} 阈值。`,
    });
  }
  if (latencyStatus !== "ok" && metrics.avgLatencyMs != null) {
    alerts.push({
      id: "latency",
      severity: latencyStatus === "danger" ? "danger" : "warning",
      title: latencyStatus === "danger" ? "平均耗时过高" : "平均耗时偏高",
      description: `今日平均耗时 ${formatDuration(metrics.avgLatencyMs)}，已超过 ${
        latencyStatus === "danger" ? "10 秒" : "2 秒"
      } 阈值。`,
    });
  }

  return { level, checks, alerts };
}
