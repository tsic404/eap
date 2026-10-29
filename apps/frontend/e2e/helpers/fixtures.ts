import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { type APIRequest, type APIRequestContext, type Page, expect } from "@playwright/test";

import { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS } from "./accounts.cjs";
import { PROXY_ORIGIN } from "./api";

// Shared plumbing for the tool/ops journeys that assert against seeded fixtures
// rather than replayed API responses. Two entry points seed the same rows and
// hand over the same manifest: `e2e/global-setup.mjs` for the whole suite and
// `scripts/e2e.sh` for the containerised runner, so the ids and expectations
// below describe the data either stack holds.

export interface FixtureTask {
  id: string;
  title: string;
  seededStatus: string;
}

export interface FixtureManifest {
  /** Fixture sets this run seeded; empty when the spec needed none. */
  seeded: string[];
  stub: {
    publicUrl: string;
    approveMarker: string;
    deniedMarker: string;
    approveDelayMs: number;
  };
  tools: { approve: string; denied: string };
  tasks: Record<"approve" | "timeout" | "reject" | "rejected" | "cancelled", FixtureTask>;
  dashboard: {
    /** Admin of the dedicated metrics tenant the dashboard rows belong to. */
    accountEmail: string;
    todayCalls: number;
    avgLatencyMs: number;
    errorRate: number;
    ranking: { name: string; calls: number }[];
  };
}

export type LocalAccount = { email: string; password?: string };

// The runner mounts its manifest at /out; the host harness has no such mount
// and gets a file under the system temp directory instead. The harness name is
// keyed by the stub port, because the manifest's stub URL and ledger belong to
// exactly one run — a concurrent run must never be read in its place.
const MANIFEST_PATH =
  process.env.E2E_FIXTURE_MANIFEST ??
  path.join(tmpdir(), `eap-e2e-fixtures-${process.env.E2E_IDP_PORT ?? "4000"}.json`);

/** Entry URL in front of the stack (the runner's `baseURL`). */
export const STACK_ORIGIN = process.env.E2E_BASE_URL ?? PROXY_ORIGIN;

/** Load the manifest this run's setup produced. */
export function loadFixtures(): FixtureManifest {
  return JSON.parse(readFileSync(MANIFEST_PATH, "utf8")) as FixtureManifest;
}

/**
 * Sign in with a seeded local account and wait for the authenticated shell.
 * Accounts come from the stack's own seed, so this exercises the real login
 * route instead of a role cookie.
 */
export async function signIn(page: Page, account: LocalAccount = LOCAL_ACCOUNTS.admin): Promise<void> {
  await page.context().addCookies([{ name: "NEXT_LOCALE", value: "zh-CN", url: STACK_ORIGIN }]);
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(account.email);
  await page.getByLabel("口令").fill(account.password ?? LOCAL_ACCOUNT_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/user");
  // The bootstrap refresh rotates the cookie; waiting for it keeps a following
  // hard navigation from replaying a superseded credential.
  await expect(page.getByText(account.email)).toBeVisible();
}

/** Independent API context authenticated as `account` (its own cookie jar). */
export async function openApiSession(
  request: APIRequest,
  account: LocalAccount = LOCAL_ACCOUNTS.admin,
): Promise<APIRequestContext> {
  const context = await request.newContext({ baseURL: STACK_ORIGIN });
  const login = await context.post("/api/auth/login/local", {
    data: { email: account.email, password: account.password ?? LOCAL_ACCOUNT_PASSWORD },
  });
  if (login.status() !== 200) {
    throw new Error(`login failed for ${account.email}: ${login.status()} ${await login.text()}`);
  }
  const refresh = await context.post("/api/auth/refresh");
  const body = (await refresh.json()) as { data?: { accessToken?: string } };
  const token = body.data?.accessToken;
  if (refresh.status() !== 200 || !token) {
    throw new Error(`refresh failed for ${account.email}: ${refresh.status()}`);
  }
  // The bearer is set at creation time (APIRequestContext has no setter in this
  // Playwright release), so the login context is replaced by an authenticated one.
  await context.dispose();
  return request.newContext({
    baseURL: STACK_ORIGIN,
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  });
}

/** One task as `GET /api/tasks/{id}` reports it (snake_case wire shape). */
export interface TaskPayload {
  id: string;
  status: string;
  result: Record<string, unknown> | null;
  error_message: string | null;
}

/** Read a task through the platform API. */
export async function fetchTask(api: APIRequestContext, taskId: string): Promise<TaskPayload> {
  const response = await api.get(`/api/tasks/${taskId}`);
  expect(response.status(), await response.text()).toBe(200);
  const body = (await response.json()) as { data: TaskPayload };
  return body.data;
}

/** Calls the stub upstream received for `marker` (newest last). */
export async function stubCalls(
  request: APIRequest,
  publicUrl: string,
  marker: string,
): Promise<{ marker: string; delayMs: number; statusCode: number }[]> {
  const context = await request.newContext();
  const response = await context.get(`${publicUrl}/calls`, { params: { marker } });
  const body = (await response.json()) as {
    calls: { marker: string; delayMs: number; statusCode: number }[];
  };
  await context.dispose();
  return body.calls;
}

/** Forget every call the stub upstream recorded. */
export async function resetStubCalls(request: APIRequest, publicUrl: string): Promise<void> {
  const context = await request.newContext();
  const response = await context.post(`${publicUrl}/reset`);
  expect(response.status()).toBe(200);
  await context.dispose();
}

/** Card wrapper around a heading: every card is a `div.rounded-lg`. */
export function cardByHeading(page: Page, name: string) {
  return page
    .getByRole("heading", { name, exact: true })
    .locator('xpath=ancestor::div[contains(@class,"rounded-lg")][1]');
}

/** Value rendered next to a metric card's label (not the health panel's copy). */
export function metricValue(page: Page, label: string) {
  return page
    .locator("div.flex.min-w-0.flex-col")
    .filter({ has: page.getByText(label, { exact: true }) })
    .locator("span")
    .nth(1);
}
