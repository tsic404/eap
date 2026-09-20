import { withSentryConfig } from "@sentry/nextjs/config";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
};

// Compose outermost-first: Sentry wraps the Next.js config produced by the
// next-intl plugin (which registers the i18n request config + message bundling).
export default withSentryConfig(withNextIntl(nextConfig), {
  // No DSN/auth token is committed in this repo, so disable source map upload
  // so the build never attempts it; error/performance capture still works.
  sourcemaps: { disable: true },
  telemetry: false,
  silent: !process.env.CI,
});
