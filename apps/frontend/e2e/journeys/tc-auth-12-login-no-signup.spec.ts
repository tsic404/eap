import { expect, test, type Page } from "@playwright/test";

import { envelope } from "../helpers/api";

// TC-auth-12 (AC-48, F7 negative): the login page never offers a self-service
// sign-up — in either capability state. The remaining clauses of the case
// (unauthenticated account creation, creation by a non-admin role, the
// deployment-time bootstrap of the first administrator) are judged by the
// backend and deployment acceptance suites.

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
