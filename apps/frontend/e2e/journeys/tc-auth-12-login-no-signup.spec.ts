import { expect, test, type Page } from "@playwright/test";

import { envelope } from "../helpers/api";
import { LOCAL_ACCOUNTS, LOCAL_ACCOUNT_PASSWORD } from "../helpers/accounts.cjs";

// TC-auth-12 (AC-48, F7 negative): the login page never offers a self-service
// sign-up — in either capability state — and neither does the member-management
// page. The remaining clauses of the case (unauthenticated account creation,
// creation by a non-admin role, the deployment-time bootstrap of the first
// administrator) are judged by the backend and deployment acceptance suites.

const SIGN_UP_PATTERN = /注册|register|sign-?up/i;

async function expectNoSignUpEntry(page: Page): Promise<void> {
  await expect(page.getByText(SIGN_UP_PATTERN)).toHaveCount(0);
  await expect(page.getByRole("link", { name: SIGN_UP_PATTERN })).toHaveCount(0);
  await expect(page.getByRole("button", { name: SIGN_UP_PATTERN })).toHaveCount(0);
}

test("TC-auth-12: 开启态登录页无注册入口", async ({ page }) => {
  await page.goto("/login");

  // The harness stack declares the capability on, so both entries render.
  await expect(page.getByLabel("邮箱")).toBeVisible();
  await expect(page.getByRole("button", { name: "SSO 登录" })).toBeVisible();
  await expectNoSignUpEntry(page);
});

test("TC-auth-12: 关闭态登录页无注册入口", async ({ page }) => {
  await page.route("**/api/auth/capabilities", (route) =>
    route.fulfill({ json: envelope({ localLoginEnabled: false }) }),
  );

  await page.goto("/login");

  await expect(page.getByRole("button", { name: "SSO 登录" })).toBeVisible();
  await expect(page.getByLabel("邮箱")).toHaveCount(0);
  await expectNoSignUpEntry(page);
});

test("TC-auth-12 members: 成员管理页无注册入口", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(LOCAL_ACCOUNTS.platformAdmin.email);
  await page.getByLabel("口令").fill(LOCAL_ACCOUNT_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/user");
  // Wait for the profile before leaving: the landing page's bootstrap refresh
  // rotates the refresh cookie, and an aborted rotation would leave the
  // browser holding the rotated-out one.
  await expect(page.getByText(LOCAL_ACCOUNTS.platformAdmin.email)).toBeVisible();

  await page.goto("/admin/users");
  // The header renders the profile only after this document's auth bootstrap
  // finished; the member page's first data call can beat it and 401.
  await expect(page.getByTitle(LOCAL_ACCOUNTS.platformAdmin.name)).toBeVisible();
  await expect(page.getByRole("heading", { name: "成员管理" })).toBeVisible();
  await expectNoSignUpEntry(page);
});
