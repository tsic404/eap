import { expect, test, type Page } from "@playwright/test";

import { LOCAL_ACCOUNTS, LOCAL_ACCOUNT_PASSWORD } from "../helpers/accounts.cjs";

// TC-auth-14 (AC-49): the top bar carries one entry per area — the user
// workspace offers "管理端" with a role-specific landing page, the admin area
// offers "用户工作区" back to the user workspace home — and never a two-item
// workspace switch whose selection only rewrites its own label.

const ADMIN_ENTRIES = [
  {
    account: LOCAL_ACCOUNTS.platformAdmin,
    landing: "/admin",
    heading: "仪表盘",
  },
  {
    account: LOCAL_ACCOUNTS.admin,
    landing: "/admin",
    heading: "仪表盘",
  },
  {
    account: LOCAL_ACCOUNTS.knowledgeAdmin,
    landing: "/admin/knowledge",
    heading: "知识库",
  },
  {
    account: LOCAL_ACCOUNTS.auditor,
    landing: "/admin/run-logs",
    heading: "运行日志",
  },
] as const;

async function signIn(
  page: Page,
  email: string,
  password: string = LOCAL_ACCOUNT_PASSWORD,
): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("口令").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/user");
  // Wait for the bootstrap refresh: navigating away mid-rotation would leave
  // the browser holding the rotated-out refresh cookie.
  await expect(page.getByText(email)).toBeVisible({ timeout: 30_000 });
}

/** No two-item workspace switch and no control that only rewrites its label. */
async function expectNoWorkspaceSwitch(page: Page): Promise<void> {
  const header = page.locator("header");
  await expect(header.getByRole("button", { name: "用户工作区" })).toHaveCount(0);
  await expect(header.getByRole("button", { name: "管理工作区" })).toHaveCount(0);
  await expect(header.getByRole("menuitem", { name: "管理工作区" })).toHaveCount(0);
  await expect(header.getByText("管理工作区")).toHaveCount(0);
}

for (const { account, landing, heading } of ADMIN_ENTRIES) {
  test(`TC-auth-14 ${account.role}: 管理端入口按角色落地并可返回用户工作区`, async ({
    page,
  }) => {
    test.setTimeout(120_000);
    await signIn(page, account.email);

    // The name stays in the top bar and the entry names its destination.
    const header = page.locator("header");
    await expect(header.getByTitle(account.name)).toBeVisible();
    const entry = header.getByRole("link", { name: "管理端" });
    await expect(entry).toHaveAttribute("href", landing);
    await expectNoWorkspaceSwitch(page);

    await entry.click();
    await page.waitForURL((url) => url.pathname === landing);
    await expect(page.getByLabel("管理工作区导航")).toBeVisible();
    // Read the landing page's identity from a fresh document: the dashboard
    // body mounts only after the document load, and the dev harness's first
    // chunk compile can leave it pending on a client-side transition.
    await page.reload();
    await expect(page.getByRole("heading", { name: heading })).toBeVisible({
      timeout: 60_000,
    });
    // The role's own admin page, not the permission notice.
    await expect(page.getByRole("heading", { name: "无管理权限" })).toHaveCount(0);

    // The same roles get one entry back to the user workspace home.
    const backEntry = header.getByRole("link", { name: "用户工作区" });
    await expect(backEntry).toHaveAttribute("href", "/user");
    await expect(header.getByRole("link", { name: "管理端" })).toHaveCount(0);
    await expectNoWorkspaceSwitch(page);

    await backEntry.click();
    await page.waitForURL((url) => url.pathname === "/user");
    await expect(page.getByRole("heading", { name: "用户工作区" })).toBeVisible();
  });
}

test("TC-auth-14 employee: 不呈现管理端入口且直访管理端被改写", async ({ page }) => {
  test.setTimeout(120_000);
  const employee = LOCAL_ACCOUNTS.employee;
  await signIn(page, employee.email);

  const header = page.locator("header");
  await expect(header.getByTitle(employee.name)).toBeVisible();
  await expect(header.getByRole("link", { name: "管理端" })).toHaveCount(0);
  await expectNoWorkspaceSwitch(page);

  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "无管理权限" })).toBeVisible();
});
