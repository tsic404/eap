import * as Sentry from "@sentry/nextjs";

import { parseTracesSampleRate } from "./lib/sentry-config";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  tracesSampleRate: parseTracesSampleRate(
    process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE,
  ),
});
