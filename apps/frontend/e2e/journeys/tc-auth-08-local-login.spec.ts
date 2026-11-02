import { setTimeout as sleep } from "node:timers/promises";

import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
  type Page,
} from "@playwright/test";

import { LOCAL_ACCOUNTS, LOCAL_ACCOUNT_PASSWORD } from "../helpers/accounts.cjs";

// TC-auth-08 (AC-45): a local account signs in with its email and password on the
// login page and lands the user workspace with the entries its role grants. The
// session it opens is the existing one — the refresh credential stays an
// HttpOnly cookie and the access token still arrives through the refresh channel
// only (C-2/C-3).

const REFRESH_COOKIE = "refresh_token";
const ADMIN = LOCAL_ACCOUNTS.admin;
const EMPLOYEE = LOCAL_ACCOUNTS.employee;

// The login throttle is one shared bucket per client IP (C-10), and every
// sign-in in this file spends from it. The lockout case needs six fresh
// attempts of its own, so it waits out the bucket's own refusal instead of
// turning it into a failure or depending on what the earlier cases left.
// At the contract 10/min one token returns every 6 s, and the lockout counter
// still advances only on real 401 answers (C-112).
const LOGIN_REFILL_MS = 6_500;
const THROTTLE_RETRIES = 10;

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

/** Post one wrong-password attempt, waiting out the shared client-IP throttle. */
async function postWrongPassword(request: APIRequestContext, email: string): Promise<APIResponse> {
  for (let retry = 0; ; retry += 1) {
    const response = await request.post("/api/auth/login/local", {
      data: { email, password: "wrong-password" },
    });
    if (response.status() !== 429) {
      return response;
    }
    const body = (await response.json()) as { error?: { code?: string } };
    if (body.error?.code !== "RATE_LIMITED") {
      return response;
    }
    if (retry === THROTTLE_RETRIES) {
      throw new Error("the login throttle did not refill for the lockout case");
    }
    await sleep(LOGIN_REFILL_MS);
  }
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
  expect(refreshCookie?.path).toBe("/api/auth/refresh");

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
  // An empty bucket stretches this case to one refill wait per token.
  test.setTimeout(180_000);

  // A fresh identifier per run: the lock lasts 15 minutes, so a long-lived
  // stack must not hand the next run an address an earlier run already locked.
  const email = `lock-${Date.now()}-${Math.floor(Math.random() * 1_000_000)}@acme.com`;

  for (let attempt = 0; attempt < 5; attempt += 1) {
    const response = await postWrongPassword(request, email);
    expect(response.status()).toBe(401);
  }

  // The identifier is locked, so every granted attempt answers LOGIN_LOCKED;
  // the bucket's own refusal may arrive first while it refills (C-10, C-112).
  const alert = page.locator("form").getByRole("alert");
  for (let retry = 0; ; retry += 1) {
    await signIn(page, email);
    await expect(alert).toBeVisible();
    const message = (await alert.textContent())?.trim() ?? "";
    if (message.includes("15 分钟")) {
      break;
    }
    expect(message).toBe("登录尝试过于频繁，请稍后再试");
    if (retry === THROTTLE_RETRIES) {
      throw new Error("the login throttle did not refill for the lockout case");
    }
    await sleep(LOGIN_REFILL_MS);
  }

  await expect(alert).toContainText("15 分钟");
});
