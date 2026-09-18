import { withSentryConfig } from "@sentry/nextjs/config";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
};

export default withSentryConfig(nextConfig, {
  // No DSN/auth token is committed in this repo, so disable source map upload
  // so the build never attempts it; error/performance capture still works.
  sourcemaps: { disable: true },
  telemetry: false,
  silent: !process.env.CI,
});
