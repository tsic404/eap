import { expect, request, test, type Page } from "@playwright/test";

import { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";
import { PROXY_ORIGIN } from "../helpers/api";

// TC-auth-02 (AC-02), browser-side evidence: a signed-in browser resumes its
// session on a hard navigation instead of stalling on the auth bootstrap, and a
// signed-in visit to the login page never opens the identity provider.
//
// The stalling case is a race, not a slow backend: the bootstrap refresh is in
// flight when the next document starts, so the request that rotated the cookie
// must survive the navigation. A regression leaves the page on "正在完成登录…"
// while the refresh endpoint answers 409 `TOKEN_REFRESH_IN_PROGRESS` until the
// C-25 window closes.

const ACCOUNT = LOCAL_ACCOUNTS.admin;
const REFRESH_PATH = "/api/auth/refresh";
const LOGIN_PENDING_MESSAGE = "正在完成登录…";

/** Every refresh answer this page sees, so a conflict streak is data, not a timeout. */
function trackRefresh(page: Page): number[] {
  const statuses: number[] = [];
  page.on("response", (response) => {
    if (new URL(response.url()).pathname === REFRESH_PATH) {
      statuses.push(response.status());
    }
  });
  return statuses;
}

/** The next refresh request, so a navigation can be aimed at its in-flight window. */
function nextRefreshRequest(page: Page) {
  return page.waitForEvent("request", (request) => {
    return new URL(request.url()).pathname === REFRESH_PATH;
  });
}

async function signIn(page: Page): Promise<void> {
  await page.context().addCookies([{ name: "NEXT_LOCALE", value: "zh-CN", url: PROXY_ORIGIN }]);
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(ACCOUNT.email);
  await page.getByLabel("口令").fill(LOCAL_ACCOUNT_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  // Deliberately no pause for the bootstrap: navigating this early is what used
  // to abort the in-flight refresh after the server had rotated the cookie.
  await page.waitForURL("**/user");
}

test("TC-auth-02 硬导航: 令牌引导期间导航仍落业务页且刷新不再冲突", async ({ page }) => {
  const refreshStatuses = trackRefresh(page);
  await signIn(page);

  // Aim the second hard navigation at the bootstrap refresh's in-flight window.
  const inFlightRefresh = nextRefreshRequest(page);
  await page.goto("/user");
  await inFlightRefresh;
  await page.goto("/user");

  await expect(page.getByText(ACCOUNT.email)).toBeVisible();
  await expect(page.getByText(LOGIN_PENDING_MESSAGE)).toHaveCount(0);
  expect(refreshStatuses.filter((status) => status === 409)).toEqual([]);
});

test("TC-auth-02 管理端硬导航: 受保护路径不常驻登录等待态", async ({ page }) => {
  const refreshStatuses = trackRefresh(page);
  await signIn(page);
  await expect(page.getByText(ACCOUNT.email)).toBeVisible();

  await page.goto("/admin/tools");

  await expect(page.getByRole("heading", { name: "工具", level: 1 })).toBeVisible();
  await expect(page.getByText(LOGIN_PENDING_MESSAGE)).toHaveCount(0);
  expect(refreshStatuses.filter((status) => status === 409)).toEqual([]);
});

test("TC-auth-02 登录页: 已登录直接进入工作区而不过身份提供方", async ({ page }) => {
  await signIn(page);
  await expect(page.getByText(ACCOUNT.email)).toBeVisible();

  await page.goto("/login");

  await page.waitForURL("**/user");
  await expect(page.getByText(ACCOUNT.email)).toBeVisible();
});

test("TC-auth-02 连续整页重载: 每次重载都在上一次的刷新在途窗口内发生，会话不被丢弃", async ({
  page,
  context,
}) => {
  const refreshStatuses = trackRefresh(page);
  await signIn(page);
  await page.goto("/admin/tools");
  await expect(page.getByRole("heading", { name: "工具", level: 1 })).toBeVisible();

  // The anonymous landing is answered 401 UNAUTHORIZED before anyone signs in;
  // from here on every refresh answer belongs to the live session.
  refreshStatuses.length = 0;

  for (let round = 0; round < 10; round += 1) {
    // Each reload is aimed at the bootstrap refresh the document just issued:
    // the document dies while its rotation is in flight, so the next document
    // owns the handoff. Landing anywhere but the target page is the defect.
    const inFlight = nextRefreshRequest(page);
    await page.goto("/admin/tools");
    await inFlight;
    await page.goto("/admin/tools");
  }

  await expect(page.getByRole("heading", { name: "工具", level: 1 })).toBeVisible();
  const roleCookie = (await context.cookies()).find((cookie) => cookie.name === "eap_role");
  expect(roleCookie?.value).toBe("agent_admin");
  expect(refreshStatuses.filter((status) => status === 409)).toEqual([]);
  // A replay beyond C-25's window revokes the whole family; the session must
  // never reach it (the answer carries TOKEN_REUSE_DETECTED).
  expect(refreshStatuses.filter((status) => status === 401)).toEqual([]);
  await expect(page.getByRole("heading", { name: "工具", level: 1 })).toBeVisible();
});

test("TC-auth-02 凭据被外部消费: 后继 Cookie 未落地时连续重载不丢会话", async ({ page, context }) => {
  const refreshStatuses = trackRefresh(page);
  await signIn(page);
  await page.goto("/admin/tools");
  await expect(page.getByRole("heading", { name: "工具", level: 1 })).toBeVisible();
  refreshStatuses.length = 0;

  // Take the browser's credential and rotate it outside the browser: the
  // successor never reaches this cookie jar, which is the page's unrecoverable
  // case — every later submit is answered with the C-25 conflict.
  const held = (await context.cookies()).find((cookie) => cookie.name === "refresh_token");
  expect(held?.value).toBeTruthy();
  const consumer = await request.newContext();
  const rotated = await consumer.post(`${PROXY_ORIGIN}/api/auth/refresh`, {
    headers: { cookie: `refresh_token=${held?.value}` },
  });
  expect(rotated.status()).toBe(200);
  const successor = /refresh_token=([^;]+)/.exec(rotated.headers()["set-cookie"] ?? "")?.[1];
  expect(successor).toBeTruthy();

  for (let round = 0; round < 10; round += 1) {
    await page.goto("/admin/tools");
    expect(new URL(page.url()).pathname).toBe("/admin/tools");
  }

  // Let the last document finish its bounded attempt: what it decides after
  // the window runs out is the whole point of this case.
  await page.waitForTimeout(4_000);

  const roleCookie = (await context.cookies()).find((cookie) => cookie.name === "eap_role");
  expect(roleCookie?.value).toBe("agent_admin");
  // A conflict is not a refusal: the channel gives up inside the window and
  // the next document tries again — it never replays past the window.
  expect(refreshStatuses.filter((status) => status === 401)).toEqual([]);
  expect(new URL(page.url()).pathname).toBe("/admin/tools");

  // The backend never ended the session: the successor still mints a pair.
  const replay = await consumer.post(`${PROXY_ORIGIN}/api/auth/refresh`, {
    headers: { cookie: `refresh_token=${successor}` },
  });
  expect(replay.status()).toBe(200);
  await consumer.dispose();
});
