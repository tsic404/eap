import { expect, test, type Page } from "@playwright/test";

import { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";

// TC-auth-11 (AC-48), frontend judgement: a platform administrator maintains the
// roster from the admin member page — creating a local account, switching a
// member's status and resetting a password — and every one of those outcomes is
// observable in the list. A role other than platform administrator gets no entry
// and no management page.

const PLATFORM_ADMIN = LOCAL_ACCOUNTS.platformAdmin;
const RESET_TARGET = LOCAL_ACCOUNTS.resetTarget;

async function signIn(page: Page, email: string, password = LOCAL_ACCOUNT_PASSWORD) {
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("口令").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/user");
  // Wait for the profile: the landing page's bootstrap refresh rotates the
  // refresh cookie, and navigating away mid-rotation would leave the browser
  // holding the rotated-out one (the next page load then reads as signed out).
  await expect(page.getByText(email)).toBeVisible();
}

function memberRow(page: Page, email: string) {
  return page.getByRole("row").filter({ hasText: email });
}

/**
 * Wait for the document's own auth bootstrap to finish: the header only shows
 * the profile once the refresh rotation has completed and /api/me resolved.
 * Navigating away mid-rotation would leave the browser holding a rotated-out
 * refresh cookie, and the next load would read as signed out.
 */
async function waitForSession(page: Page, name: string) {
  await expect(page.getByTitle(name)).toBeVisible();
}

test("TC-auth-11 create: 管理员创建的本地账号出现在成员列表且来源与须改密可见", async ({
  page,
}) => {
  const email = `local-${Date.now()}@acme.com`;
  await signIn(page, PLATFORM_ADMIN.email);

  // The sidebar's user entry is the real member-management route, not a placeholder.
  await page.goto("/admin/users");
  await waitForSession(page, PLATFORM_ADMIN.name);
  await expect(page.getByRole("link", { name: "用户管理" })).toHaveAttribute(
    "href",
    "/admin/users",
  );
  await expect(page.getByRole("heading", { name: "成员管理" })).toBeVisible();

  await page.getByRole("button", { name: "新建本地账号" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("邮箱").fill(email);
  await dialog.getByLabel("姓名").fill("E2E Local");
  await dialog.getByLabel("初始口令").fill("Local-Pass-9");
  await dialog.getByRole("button", { name: "创建" }).click();

  const row = memberRow(page, email);
  await expect(row).toBeVisible();
  await expect(row.getByText("本地")).toBeVisible();
  await expect(row.getByText("须改密")).toBeVisible();
});

test("TC-auth-11 status: 停用后状态可见且刷新后仍为已停用", async ({ page }) => {
  await signIn(page, PLATFORM_ADMIN.email);
  await page.goto("/admin/users");
  await waitForSession(page, PLATFORM_ADMIN.name);

  const row = memberRow(page, RESET_TARGET.email);
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "停用" }).click();

  await expect(row.getByText("已停用")).toBeVisible();
  await page.reload();
  await expect(memberRow(page, RESET_TARGET.email).getByText("已停用")).toBeVisible();
});

test("TC-auth-11 password: 口令重置后该账号重新置为须改密", async ({ page }) => {
  await signIn(page, PLATFORM_ADMIN.email);
  await page.goto("/admin/users");

  // The seeded target starts out not needing a change, so the reset must flip it.
  const row = memberRow(page, RESET_TARGET.email);
  await expect(row).toBeVisible();
  await expect(row.getByText("无需改密")).toBeVisible();

  await row.getByRole("button", { name: "重置口令" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("新口令").fill("Reset-Pass-9");
  await dialog.getByRole("button", { name: "重置" }).click();

  await expect(page.getByText("口令已重置，该账号下次登录须改密")).toBeVisible();
  await expect(row.getByText("须改密")).toBeVisible();
});

test("TC-auth-11 denied: 非平台管理员无成员管理入口且被拒", async ({ page }) => {
  await signIn(page, LOCAL_ACCOUNTS.admin.email);

  await page.goto("/admin");
  await waitForSession(page, LOCAL_ACCOUNTS.admin.name);
  await expect(page.getByRole("link", { name: "用户管理" })).toHaveCount(0);

  await page.goto("/admin/users");
  await expect(page.getByRole("heading", { name: "无管理权限" })).toBeVisible();
  await expect(page.getByRole("button", { name: "新建本地账号" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重置口令" })).toHaveCount(0);
});
