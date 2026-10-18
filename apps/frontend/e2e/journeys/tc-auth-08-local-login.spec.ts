import { expect, test, type Page } from "@playwright/test";

import { LOCAL_ACCOUNTS, LOCAL_ACCOUNT_PASSWORD } from "../helpers/accounts.cjs";

// TC-auth-08 (AC-45): a local account signs in with its email and password on the
// login page and lands the user workspace with the entries its role grants. The
// session it opens is the existing one — the refresh credential stays an
// HttpOnly cookie and the access token still arrives through the refresh channel
// only (C-2/C-3).

const REFRESH_COOKIE = "refresh_token";
const ADMIN = LOCAL_ACCOUNTS.admin;
const EMPLOYEE = LOCAL_ACCOUNTS.employee;

async function signIn(
  page: Page,
  email: string,
  password: string = LOCAL_ACCOUNT_PASSWORD,
): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("口令").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
}

test("TC-auth-08 landing: 本地账号登录成功并落地用户工作区", async ({ page }) => {
  await signIn(page, ADMIN.email);

  await page.waitForURL("**/user");
  await expect(page.getByText(ADMIN.email)).toBeVisible();
  // The name renders in the profile card and its containers — assert the first match.
  await expect(page.getByText(ADMIN.name).first()).toBeVisible();
  // The header offers the workspaces this account's role admits.
  await page.getByRole("button", { name: "用户工作区" }).click();
  await expect(page.getByRole("menuitem", { name: "管理工作区" })).toBeVisible();
});

test("TC-auth-08 role: 登录方式不改变角色对应入口", async ({ page }) => {
  await signIn(page, EMPLOYEE.email);

  await page.waitForURL("**/user");
  await expect(page.getByText(EMPLOYEE.email)).toBeVisible();

  // An employee gets no admin workspace entry, and the admin route still serves
  // the friendly permission notice — the same judgement the SSO path makes.
  await page.getByRole("button", { name: "用户工作区" }).click();
  await expect(page.getByRole("menuitem", { name: "管理工作区" })).toHaveCount(0);

  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "无管理权限" })).toBeVisible();
});

test("TC-auth-08 credentials: 凭据形态与既有路径一致", async ({ page, context }) => {
  await signIn(page, ADMIN.email);
  await page.waitForURL("**/user");
  await expect(page.getByText(ADMIN.email)).toBeVisible();

  const refreshCookie = (await context.cookies()).find((c) => c.name === REFRESH_COOKIE);
  expect(refreshCookie).toBeTruthy();
  expect(refreshCookie?.httpOnly).toBe(true);
  expect(refreshCookie?.sameSite).toBe("Lax");
  expect(refreshCookie?.path).toBe("/api/auth");

  // Page scripts cannot read the refresh credential, and nothing is persisted.
  const scriptVisible = await page.evaluate(() => ({
    cookies: document.cookie,
    local: Object.keys(localStorage).length,
    session: Object.keys(sessionStorage).length,
  }));
  expect(scriptVisible.cookies).not.toContain(`${REFRESH_COOKIE}=`);
  expect(scriptVisible.local).toBe(0);
  expect(scriptVisible.session).toBe(0);

  // The access token still comes only from the refresh channel (900s TTL).
  const refreshed = await page.evaluate(async () => {
    const response = await fetch("/api/auth/refresh", {
      method: "POST",
      credentials: "include",
    });
    return {
      status: response.status,
      body: (await response.json()) as { data: { expiresIn: number } },
    };
  });
  expect(refreshed.status).toBe(200);
  expect(refreshed.body.data.expiresIn).toBe(900);
});

test("TC-auth-08 errors: 凭据与租户错误给出可读提示", async ({ page }) => {
  // Scoped to the form: Next.js mounts its own route announcer as an alert.
  await signIn(page, ADMIN.email, "wrong-password");
  await expect(page.locator("form").getByRole("alert")).toHaveText("邮箱或口令不正确");

  await signIn(page, "nobody@nomatch.example");
  await expect(page.locator("form").getByRole("alert")).toHaveText(
    "该邮箱所属的租户不存在，请联系管理员",
  );
});

test("TC-auth-08 lockout: 连续失败后锁定并给出 15 分钟口径", async ({ page, request }) => {
  const email = "lock-me@acme.com";
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const response = await request.post("/api/auth/login/local", {
      data: { email, password: "wrong-password" },
    });
    expect(response.status()).toBe(401);
  }

  await signIn(page, email);

  await expect(page.locator("form").getByRole("alert")).toContainText("15 分钟");
});
