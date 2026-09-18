"use client";

import { useReportWebVitals } from "next/web-vitals";

import * as Sentry from "@sentry/nextjs";

const METRIC_UNITS: Record<string, string> = {
  LCP: "millisecond",
  INP: "millisecond",
  CLS: "none",
  FCP: "millisecond",
  FID: "millisecond",
  TTFB: "millisecond",
};

/**
 * Reports Core Web Vitals (LCP/INP/CLS, §30.11) as Sentry distribution
 * metrics. Mounted once in the root layout; renders nothing.
 */
export function WebVitals() {
  useReportWebVitals((metric) => {
    Sentry.metrics.distribution(`web_vitals.${metric.name.toLowerCase()}`, metric.value, {
      unit: METRIC_UNITS[metric.name] ?? "none",
    });
  });
  return null;
}
