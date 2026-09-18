import { defineConfig } from "@playwright/test";
import { generateKeyPairSync } from "node:crypto";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(dirname, ".");
const repoRoot = path.resolve(dirname, "../..");
const backendDir = path.join(repoRoot, "apps/backend");

// The backend signs access tokens with an RS256 keypair it reads from env;
// generate a fresh one per run (signing and verification happen in the same
// process, so a per-run pair is sufficient).
const { privateKey, publicKey } = generateKeyPairSync("rsa", {
  modulusLength: 2048,
  publicKeyEncoding: { type: "spki", format: "pem" },
  privateKeyEncoding: { type: "pkcs8", format: "pem" },
});

const DATABASE_URL =
  process.env.E2E_DATABASE_URL ??
  "postgresql+asyncpg://eap:eap_password@localhost:5432/eap_e2e";
const REDIS_URL = process.env.E2E_REDIS_URL ?? "redis://localhost:6379/0";
// Prefer the project venv; fall back to `python` on PATH (CI installs system-wide).
const venvPython = path.join(backendDir, ".venv/bin/python");
const BACKEND_PYTHON =
  process.env.E2E_BACKEND_PYTHON ?? (existsSync(venvPython) ? venvPython : "python");

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.mjs",
  use: {
    baseURL: "http://localhost:8090",
    channel: "chrome",
    headless: true,
  },
  webServer: [
    {
      command: "node e2e/mock-idp.mjs",
      cwd: frontendDir,
      url: "http://localhost:4000/.well-known/openid-configuration",
      reuseExistingServer: false,
    },
    {
      command: `${BACKEND_PYTHON} -m uvicorn app.main:app --host 127.0.0.1 --port 3001`,
      cwd: backendDir,
      url: "http://localhost:3001/api/health/live",
      reuseExistingServer: false,
      env: {
        ...process.env,
        PATH: `${path.join(backendDir, ".venv/bin")}:${process.env.PATH ?? ""}`,
        DATABASE_URL,
        REDIS_URL,
        OIDC_ISSUER: "http://localhost:4000",
        OIDC_CLIENT_ID: "e2e-client",
        OIDC_REDIRECT_URI: "http://localhost:8090/api/auth/callback",
        JWT_PRIVATE_KEY: privateKey,
        JWT_PUBLIC_KEY: publicKey,
        // The proxy origin is the browser's same-origin; the backend's CORS
        // whitelist must admit it or same-origin POSTs (refresh/logout) are
        // rejected with 403.
        CORS_ALLOWED_ORIGINS: JSON.stringify(["http://localhost:8090", "http://localhost"]),
      },
    },
    {
      command: "pnpm dev",
      cwd: frontendDir,
      url: "http://localhost:3000",
      reuseExistingServer: false,
      timeout: 180_000,
    },
    {
      command: "node e2e/reverse-proxy.mjs",
      cwd: frontendDir,
      url: "http://localhost:8090",
      reuseExistingServer: false,
    },
  ],
});
