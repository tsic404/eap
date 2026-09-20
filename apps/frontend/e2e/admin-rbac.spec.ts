import { expect, test, type Page } from "@playwright/test";

// RBAC gate e2e (§31.2.2): a real OIDC login must land the browser's
// `eap_role` cookie so the middleware admits admins to /admin and serves
// employees the /forbidden notice.

const ROLE_COOKIE = "eap_role";
const ROLE_TTL_SECONDS = 900;

async function loginViaSso(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "SSO 登录" }).click();
  await page.waitForURL("**/user");
}

test("agent_admin completes OIDC and reaches /admin", async ({ page, context }) => {
  await loginViaSso(page);
  await expect(page.getByText("alice@acme.com")).toBeVisible();

  // loadUser() mirrored the backend role into the eap_role cookie with the
  // documented attributes (SameSite=Lax, Path=/, Max-Age=access-token TTL).
  const cookie = (await context.cookies()).find((c) => c.name === ROLE_COOKIE);
  expect(cookie).toBeTruthy();
  expect(cookie!.value).toBe("agent_admin");
  expect(cookie!.sameSite).toBe("Lax");
  expect(cookie!.path).toBe("/");
  const ttl = cookie!.expires - Math.floor(Date.now() / 1000);
  expect(Math.abs(ttl - ROLE_TTL_SECONDS)).toBeLessThan(60);

  await page.goto("/admin");
  await expect(page.getByLabel("管理工作区导航")).toBeVisible();
  await expect(page.getByText("无管理权限")).toHaveCount(0);
});

test("employee completes OIDC and is served the forbidden notice", async ({
  page,
  context,
}) => {
  // Select the employee identity (bob@acme.com) for the authorize request.
  await context.addCookies([
    { name: "mock_idp_user", value: "bob", url: "http://localhost:4000" },
  ]);

  await loginViaSso(page);
  await expect(page.getByText("bob@acme.com")).toBeVisible();

  const cookie = (await context.cookies()).find((c) => c.name === ROLE_COOKIE);
  expect(cookie).toBeTruthy();
  expect(cookie!.value).toBe("employee");

  await page.goto("/admin");
  await expect(page.getByText("无管理权限")).toBeVisible();
});
