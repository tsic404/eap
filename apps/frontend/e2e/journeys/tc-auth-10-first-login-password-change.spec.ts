import { expect, test, type Page } from "@playwright/test";

import { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";
import { envelope } from "../helpers/api";

// TC-auth-10 (AC-47): a forced-change account is guided to the password page on
// sign-in, bounced back there from every business page until it changes its
// password, and the change ends every refresh credential it held — the old
// password is refused and the account signs in again with the new one (C-15 /
// C-113).

const PENDING = LOCAL_ACCOUNTS.pending;
const NEW_PASSWORD = "Rotated-Account-2";
const REFRESH_COOKIE = "refresh_token";

async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("口令").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
}

test("TC-auth-10: 首登改密引导、改密与刷新凭据失效", async ({ page, request }) => {
  await page.goto("/login");
  await signIn(page, PENDING.email, LOCAL_ACCOUNT_PASSWORD);

  await page.waitForURL("**/password");
  await expect(page.getByRole("heading", { name: "修改口令" })).toBeVisible();

  // A closed capability never blocks the signed-in account's own change
  // (C-110): the page renders its form without consulting the read point.
  await page.route("**/api/auth/capabilities", (route) =>
    route.fulfill({ json: envelope({ localLoginEnabled: false }) }),
  );
  await page.reload();
  await expect(page.getByRole("heading", { name: "修改口令" })).toBeVisible();
  await page.unroute("**/api/auth/capabilities");

  // Before the change, a business page still bounces back to the password page.
  await page.goto("/user");
  await page.waitForURL("**/password");
  await expect(page.getByRole("heading", { name: "修改口令" })).toBeVisible();

  const heldRefreshCredential = (await page.context().cookies()).find(
    (cookie) => cookie.name === REFRESH_COOKIE,
  )?.value;
  expect(heldRefreshCredential).toBeTruthy();

  await page.getByLabel("当前口令").fill(LOCAL_ACCOUNT_PASSWORD);
  await page.getByLabel("新口令", { exact: true }).fill(NEW_PASSWORD);
  await page.getByLabel("确认新口令", { exact: true }).fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "修改口令" }).click();

  await page.waitForURL("**/login");

  // Every refresh credential issued before the change is dead (C-113).
  const replayed = await request.post("/api/auth/refresh", {
    headers: { Cookie: `${REFRESH_COOKIE}=${heldRefreshCredential}` },
  });
  expect(replayed.status()).toBe(401);

  // The old password no longer signs in; the new one does, without another
  // forced-change detour. Scope the alert to the login form: the app router
  // keeps its own `role="alert"` announcer in the document.
  await signIn(page, PENDING.email, LOCAL_ACCOUNT_PASSWORD);
  await expect(page.locator("form").getByRole("alert")).toHaveText("邮箱或口令不正确");

  await page.getByLabel("口令").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();

  await page.waitForURL("**/user");
  await expect(page.getByRole("heading", { name: "用户工作区" })).toBeVisible();
  await expect(page.getByText(PENDING.email)).toBeVisible();
});

test("TC-auth-10 gate: 未登录访问改密页跳转登录页", async ({ page }) => {
  await page.goto("/password");

  await page.waitForURL("**/login");
  await expect(page.getByRole("button", { name: "SSO 登录" })).toBeVisible();
});
