const DATE_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const DATE_TIME_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

const NUMBER_FORMATTER = new Intl.NumberFormat("zh-CN");

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

const DATE_ONLY_REGEX = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Coerce a date-ish value to a local `Date`. A date-only `YYYY-MM-DD` string is
 * parsed as local midnight — `new Date("2026-01-15")` alone parses as UTC
 * midnight and shifts the calendar day for users west of UTC.
 */
function toLocalDate(value: Date | string | number): Date {
  if (value instanceof Date) return value;
  if (typeof value === "string" && DATE_ONLY_REGEX.test(value)) {
    return new Date(`${value}T00:00:00`);
  }
  return new Date(value);
}

/** Format a date as `YYYY/MM/DD`. Invalid input → empty string. */
export function formatDate(value: Date | string | number): string {
  const date = toLocalDate(value);
  return Number.isNaN(date.getTime()) ? "" : DATE_FORMATTER.format(date);
}

/** Format a date with time as `YYYY/MM/DD HH:mm`. Invalid input → empty string. */
export function formatDateTime(value: Date | string | number): string {
  const date = toLocalDate(value);
  return Number.isNaN(date.getTime()) ? "" : DATE_TIME_FORMATTER.format(date);
}

/** Format a number with thousands separators. Non-finite input → empty string. */
export function formatNumber(value: number): string {
  return Number.isFinite(value) ? NUMBER_FORMATTER.format(value) : "";
}

/** Format a byte count into a human-readable size (`1.5 MB`). */
export function formatBytes(bytes: number, decimals = 1): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes === 0) return `0 ${BYTE_UNITS[0]}`;
  // Sub-byte values must not fall through to exponent -1 (`BYTE_UNITS[-1]`).
  if (bytes < 1) return `${bytes.toFixed(decimals)} ${BYTE_UNITS[0]}`;

  const exponent = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    BYTE_UNITS.length - 1,
  );
  const value = bytes / 1024 ** exponent;
  const fixed = exponent === 0 ? 0 : decimals;
  return `${value.toFixed(fixed)} ${BYTE_UNITS[exponent]}`;
}
