import * as Sentry from "@sentry/nextjs";

export async function register() {
  // Each config pulls runtime-specific modules (node vs edge), so they cannot
  // both be statically imported — load only the one matching NEXT_RUNTIME.
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }

  if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}

// Capture errors from Server Components, middleware, and proxies.
export const onRequestError = Sentry.captureRequestError;
