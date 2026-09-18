/** Fallback traces sample rate used when the configured value is absent/invalid. */
export const DEFAULT_TRACES_SAMPLE_RATE = 0.1;

/**
 * Parses a configured traces sample rate into a number in [0, 1]. A
 * non-numeric `NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE` would otherwise yield
 * `NaN`, silently disabling tracing — fall back to the default instead.
 */
export function parseTracesSampleRate(value: string | undefined): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1) {
    return DEFAULT_TRACES_SAMPLE_RATE;
  }
  return parsed;
}
