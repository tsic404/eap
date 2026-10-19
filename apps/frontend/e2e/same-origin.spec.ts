import { expect, test, type Page } from "@playwright/test";

import { PROXY_ORIGIN } from "./helpers/api";

// TC-tech-ops-02 (AC-T10), last clause: the browser reaches the API on its own
// origin through a relative path. The backend acceptance module pins the
// deployment shape (single entry origin, backend never published, no absolute
// API base baked into the frontend); this spec is the browser-side half — every
// `/api/*` request the running page issues carries the page's own origin and a
// relative path, never the backend's port.

const AUTH_PATHS = [/^\/api\/auth\//, /^\/api\/me$/];

/** Completes SSO and waits for the `/api/me` bootstrap, without text probes. */
async function login(page: Page): Promise<void> {
  const profile = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/me" && response.ok();
  });
  await page.context().addCookies([
    { name: "NEXT_LOCALE", value: "zh-CN", url: PROXY_ORIGIN },
  ]);
  await page.goto("/login");
  await page.getByRole("button", { name: "SSO 登录" }).click();
  await page.waitForURL("**/user");
  await profile;
}

/** Navigates and waits for the page's own data request to settle. */
async function gotoAndAwaitApi(
  page: Page,
  route: string,
  apiPath: string,
): Promise<void> {
  const response = page.waitForResponse((candidate) => {
    const url = new URL(candidate.url());
    return url.pathname === apiPath && candidate.ok();
  });
  await page.goto(route);
  await response;
}

test("browser API requests stay on the page origin with a relative path", async ({
  page,
}) => {
  const proxyOrigin = new URL(PROXY_ORIGIN).origin;
  const apiRequests: { url: string; path: string }[] = [];

  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/")) return;
    apiRequests.push({ url: request.url(), path: url.pathname });
  });

  await login(page);
  await gotoAndAwaitApi(page, "/user/agents", "/api/agents");
  await gotoAndAwaitApi(page, "/user/conversations", "/api/conversations");

  expect(
    apiRequests.length,
    "the page issued at least one API request",
  ).toBeGreaterThan(0);
  expect(
    apiRequests.filter(
      (request) =>
        !AUTH_PATHS.some((pattern) => pattern.test(request.path)),
    ).length,
    "at least one non-auth API request was observed",
  ).toBeGreaterThan(0);
  for (const request of apiRequests) {
    expect(new URL(request.url).origin, request.url).toBe(proxyOrigin);
  }
  expect(new URL(page.url()).origin).toBe(proxyOrigin);
});
