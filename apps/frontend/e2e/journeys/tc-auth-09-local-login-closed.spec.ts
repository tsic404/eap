import { expect, test, type Page } from "@playwright/test";

import { envelope } from "../helpers/api";
import { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";

// TC-auth-09 (AC-46), frontend judgement: with the capability closed the login
// page offers the enterprise identity entry alone — no email/password form and
// no sign-up. The page consults one public read point (C-110), so answering it
// the way a closed deployment does pins that state; the read point's own report
// in both states and the survival of already signed-in local accounts are
// judged by the backend acceptance suite.

test("TC-auth-09: 关闭态登录页只呈现企业统一身份入口", async ({ page }) => {
  await page.route("**/api/auth/capabilities", (route) =>
    route.fulfill({ json: envelope({ localLoginEnabled: false }) }),
  );

  await page.goto("/login");

  await expect(page.getByRole("button", { name: "SSO 登录" })).toBeVisible();
  await expect(page.getByLabel("邮箱")).toHaveCount(0);
  await expect(page.getByLabel("口令")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "登录", exact: true })).toHaveCount(0);
});

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("口令").fill(LOCAL_ACCOUNT_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/user");
  // Wait for the profile: the landing page's bootstrap refresh rotates the
  // refresh cookie, and navigating away mid-rotation would leave the browser
  // holding the rotated-out one (the next page load then reads as signed out).
  await expect(page.getByText(email)).toBeVisible();
}

test("TC-auth-09 members: 关闭态成员管理不呈现本地账号创建与口令重置入口", async ({
  page,
}) => {
  // Sign in while the harness capability is on, then answer the read point as a
  // closed deployment: the existing session must keep working (C-110).
  await signIn(page, LOCAL_ACCOUNTS.platformAdmin.email);
  await page.route("**/api/auth/capabilities", (route) =>
    route.fulfill({ json: envelope({ localLoginEnabled: false }) }),
  );

  await page.goto("/admin/users");
  // The header renders the profile only after this document's auth bootstrap
  // finished; the member page's first data call can beat it and 401.
  await expect(page.getByTitle(LOCAL_ACCOUNTS.platformAdmin.name)).toBeVisible();
  await expect(page.getByRole("heading", { name: "成员管理" })).toBeVisible();

  // Existing members and the status switch stay available.
  const row = page.getByRole("row").filter({ hasText: "alice@acme.com" });
  await expect(row).toBeVisible();
  await expect(row.getByRole("button", { name: "停用" })).toBeVisible();

  await expect(page.getByRole("button", { name: "新建本地账号" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重置口令" })).toHaveCount(0);

  // An empty roster must not advertise the entry either (C-110): the prompt may
  // only point at the filters.
  await page.getByLabel("搜索成员").fill(`no-such-member-${Date.now()}`);
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.getByText("暂无成员")).toBeVisible();
  await expect(page.getByText("或创建本地账号")).toHaveCount(0);
});
