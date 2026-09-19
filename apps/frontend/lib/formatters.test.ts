import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatDate,
  formatDuration,
  formatNumber,
} from "./formatters";

describe("formatDate", () => {
  it("formats a Date as YYYY/MM/DD", () => {
    expect(formatDate(new Date(2026, 0, 15))).toBe("2026/01/15");
  });

  it("parses a date-only string as a local calendar date", () => {
    expect(formatDate("2026-01-15")).toBe("2026/01/15");
  });

  it("returns empty string for invalid input", () => {
    expect(formatDate("not-a-date")).toBe("");
  });
});

describe("formatNumber", () => {
  it("adds thousands separators", () => {
    expect(formatNumber(1234567)).toBe("1,234,567");
  });

  it("returns empty string for non-finite input", () => {
    expect(formatNumber(Number.NaN)).toBe("");
  });
});

describe("formatBytes", () => {
  it("formats zero bytes", () => {
    expect(formatBytes(0)).toBe("0 B");
  });

  it("formats sub-byte values without an undefined unit", () => {
    expect(formatBytes(0.5)).toBe("0.5 B");
  });

  it("formats kilobyte values with one decimal", () => {
    expect(formatBytes(1536)).toBe("1.5 KB");
  });

  it("formats megabyte values", () => {
    expect(formatBytes(1048576)).toBe("1.0 MB");
  });
});

describe("formatDuration", () => {
  it("returns an em dash for null, undefined, or non-finite input", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
    expect(formatDuration(Number.NaN)).toBe("—");
  });

  it("keeps millisecond precision below one second", () => {
    expect(formatDuration(0)).toBe("0 ms");
    expect(formatDuration(350)).toBe("350 ms");
  });

  it("formats seconds with one decimal", () => {
    expect(formatDuration(1500)).toBe("1.5 s");
  });

  it("formats minutes with remainder seconds", () => {
    expect(formatDuration(90_000)).toBe("1m 30s");
  });

  it("carries a rounded-up second into the next minute", () => {
    expect(formatDuration(119_500)).toBe("2m 0s");
  });
});
