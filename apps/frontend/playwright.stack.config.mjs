// Playwright configuration for an already-running orchestration.
//
// `playwright.config.mjs` starts four host processes (mock IdP, uvicorn,
// `pnpm dev`, reverse proxy) and prepares the database, which presumes a full
// host toolchain. This config targets a stack brought up by compose instead:
// the browser only needs the entry URL, which the caller passes in.
//
//   E2E_BASE_URL      browser-facing entry (default http://localhost:8090)
//   E2E_REPORT_DIR    HTML report directory (default playwright-report)
//   E2E_OUTPUT_DIR    screenshots/traces directory (default test-results)

import path from "node:path";

import { defineConfig } from "@playwright/test";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:8090";
const REPORT_DIR = path.resolve(process.env.E2E_REPORT_DIR ?? "playwright-report");
const OUTPUT_DIR = path.resolve(process.env.E2E_OUTPUT_DIR ?? "test-results");

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  outputDir: OUTPUT_DIR,
  reporter: [["list"], ["html", { outputFolder: REPORT_DIR, open: "never" }]],
  use: {
    baseURL: BASE_URL,
    // The bundled Chromium, not the host's Chrome channel: the runner image
    // ships exactly one browser and the host is not assumed to have any.
    browserName: "chromium",
    headless: true,
  },
});
