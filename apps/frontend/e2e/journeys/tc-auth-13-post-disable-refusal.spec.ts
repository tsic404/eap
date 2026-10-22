import { expect, test, type Page } from "@playwright/test";

import { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";
import { PROXY_ORIGIN } from "../helpers/api";

// TC-auth-13 (AC-48, F10 negative): once a platform administrator disables a
// local account, its correct password gets the same indistinguishable 401 as a
// wrong one and no session cookie, while the session it opened before the
// disablement completes no business request — the access token it already held
// is refused as unauthenticated and the browser returns to the login page.

const REFRESH_COOKIE = "refresh_token";
const PLATFORM_ADMIN = LOCAL_ACCOUNTS.platformAdmin;
const INITIAL_PASSWORD = "Local-Pass-9";
const ROTATED_PASSWORD = "Rotated-Account-3";
const WRONG_PASSWORD = "wrong-password";

async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("口令").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
}

/** The access token of a refresh response body, or null when it carries none. */
function accessTokenOf(body: unknown): string | null {
  if (typeof body !== "object" || body === null || !("data" in body)) return null;
  const data = body.data;
  if (typeof data !== "object" || data === null || !("accessToken" in data)) return null;
  return typeof data.accessToken === "string" ? data.accessToken : null;
}

/** The stable code of a platform error envelope (`{ error: { code } }`). */
function errorCodeOf(body: unknown): string | null {
  if (typeof body !== "object" || body === null || !("error" in body)) return null;
  const error = body.error;
  if (typeof error !== "object" || error === null || !("code" in error)) return null;
  return typeof error.code === "string" ? error.code : null;
}

/** The access token the refresh channel hands the browser (C-2 / C-3). */
async function refreshAccessToken(page: Page): Promise<string> {
  const body: unknown = await page.evaluate(async () => {
    const response = await fetch("/api/auth/refresh", {
      method: "POST",
      credentials: "include",
    });
    return response.json();
  });
  const token = accessTokenOf(body);
  if (!token) throw new Error("refresh channel returned no access token");
  return token;
}

test("TC-auth-13: 停用后登录被拒且停用前会话无法完成业务请求", async ({
  browser,
  page,
  request,
}) => {
  const email = `disabled-${Date.now()}@acme.com`;
  const memberContext = await browser.newContext({ baseURL: PROXY_ORIGIN });
  const anonymousContext = await browser.newContext({ baseURL: PROXY_ORIGIN });

  try {
    // 1. The administrator creates the local account from the member roster.
    await signIn(page, PLATFORM_ADMIN.email, LOCAL_ACCOUNT_PASSWORD);
    await page.waitForURL("**/user");
    await expect(page.getByText(PLATFORM_ADMIN.email)).toBeVisible();

    await page.goto("/admin/users");
    await expect(page.getByTitle(PLATFORM_ADMIN.name)).toBeVisible();
    await page.getByRole("button", { name: "新建本地账号" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("邮箱").fill(email);
    await dialog.getByLabel("姓名").fill("E2E Disabled");
    await dialog.getByLabel("初始口令").fill(INITIAL_PASSWORD);
    await dialog.getByRole("button", { name: "创建" }).click();
    const row = page.getByRole("row").filter({ hasText: email });
    await expect(row).toBeVisible();

    // The account clears its forced first-login change and opens a session.
    const memberPage = await memberContext.newPage();
    await signIn(memberPage, email, INITIAL_PASSWORD);
    await memberPage.waitForURL("**/password");
    await memberPage.getByLabel("当前口令").fill(INITIAL_PASSWORD);
    await memberPage.getByLabel("新口令", { exact: true }).fill(ROTATED_PASSWORD);
    await memberPage.getByLabel("确认新口令", { exact: true }).fill(ROTATED_PASSWORD);
    await memberPage.getByRole("button", { name: "修改口令" }).click();
    await memberPage.waitForURL("**/login");

    await signIn(memberPage, email, ROTATED_PASSWORD);
    await memberPage.waitForURL("**/user");
    await expect(memberPage.getByText(email)).toBeVisible();

    // Baseline while the account is still active: a wrong password's refusal is
    // the exact shape the disabled account's refusal must match. Comparing two
    // refusals of the same disabled account would only prove the answer does not
    // depend on whether the password was correct.
    const baseline = await request.post("/api/auth/login/local", {
      data: { email, password: WRONG_PASSWORD },
    });
    expect(baseline.status()).toBe(401);
    const canonicalRefusal: unknown = await baseline.json();
    expect(errorCodeOf(canonicalRefusal)).toBe("INVALID_CREDENTIALS");

    // The token this session holds reaches a business endpoint before the
    // disablement — so the later 401 is the disablement's judgement, not a
    // token that never worked.
    const preDisableToken = await refreshAccessToken(memberPage);
    const before = await request.get("/api/agents", {
      headers: { Authorization: `Bearer ${preDisableToken}` },
    });
    expect(before.status()).toBe(200);

    // 2. The administrator disables the account.
    await row.getByRole("button", { name: "停用" }).click();
    await expect(row.getByText("已停用")).toBeVisible();

    // 3. The pre-disablement session is refused as unauthenticated: the access
    // token it held no longer completes a business request, and the page hands
    // the browser back to the login page.
    const after = await request.get("/api/agents", {
      headers: { Authorization: `Bearer ${preDisableToken}` },
    });
    expect(after.status()).toBe(401);
    expect(errorCodeOf(await after.json())).toBe("UNAUTHORIZED");

    await memberPage.goto("/user");
    await memberPage.waitForURL("**/login");
    await expect(memberPage.getByRole("button", { name: "SSO 登录" })).toBeVisible();

    // 4. The correct password is refused exactly like the wrong password the
    // active account got before the disablement, without a session cookie. The
    // baseline and this refusal spend two of the five failures before the
    // identifier lockout (C-112).
    const refused = await request.post("/api/auth/login/local", {
      data: { email, password: ROTATED_PASSWORD },
    });
    expect(refused.status()).toBe(401);
    const refusedBody: unknown = await refused.json();
    expect(refusedBody).toEqual(canonicalRefusal);
    expect(errorCodeOf(refusedBody)).toBe("INVALID_CREDENTIALS");

    const anonymousPage = await anonymousContext.newPage();
    await signIn(anonymousPage, email, ROTATED_PASSWORD);
    await expect(anonymousPage.locator("form").getByRole("alert")).toHaveText(
      "邮箱或口令不正确",
    );
    await expect(anonymousPage).toHaveURL(/\/login$/);
    const cookies = await anonymousContext.cookies();
    expect(cookies.find((cookie) => cookie.name === REFRESH_COOKIE)).toBeUndefined();
  } finally {
    await memberContext.close();
    await anonymousContext.close();
  }
});
