import { expect, test } from "@playwright/test";

// SSO end-to-end regression (UC-01-1 / UC-01-4).
//
// A real browser completes the OIDC authorization-code + PKCE flow against a
// mock IdP, lands on /user, and renders the authenticated profile. The refresh
// cookie contract (HttpOnly, path=/api/auth, SameSite=Lax, 30-day Max-Age) and
// the rotation / logout-revocation behaviour are asserted against the browser's
// observable cookie store.
//
// Note: the access-token TTL is `expiresIn: 900` in the refresh response BODY;
// the refresh cookie itself carries the 30-day refresh TTL (2592000s), not 900.

const REFRESH_COOKIE = "refresh_token";
const REFRESH_TTL_SECONDS = 2592000;

test("SSO login lands on /user and honours the refresh-cookie contract", async ({
  page,
  context,
}) => {
  // 1. Start the SSO login from the frontend login page.
  await page.goto("/login");
  await page.getByRole("button", { name: "SSO 登录" }).click();

  // 2. The browser follows login → IdP authorize → callback → /user.
  await page.waitForURL("**/user");

  // 3. The frontend renders the authenticated profile from /api/me. The name
  // appears in both the header and the profile card, so target the unique
  // email and the first name match.
  await expect(page.getByText("alice@acme.com")).toBeVisible();
  await expect(page.getByText("Alice").first()).toBeVisible();

  // 4. Refresh cookie attributes (rotated once by the frontend bootstrap).
  const refreshCookie = async () =>
    (await context.cookies()).find((c) => c.name === REFRESH_COOKIE);
  const initial = await refreshCookie();
  expect(initial).toBeTruthy();
  expect(initial!.httpOnly).toBe(true);
  expect(initial!.sameSite).toBe("Lax");
  expect(initial!.path).toBe("/api/auth");
  const ttl = initial!.expires - Math.floor(Date.now() / 1000);
  expect(Math.abs(ttl - REFRESH_TTL_SECONDS)).toBeLessThan(60);

  // 5. Refresh rotates the cookie and returns expiresIn: 900 in the body.
  const before = initial!.value;
  const refreshed = await page.evaluate(async () => {
    const res = await fetch("/api/auth/refresh", { method: "POST", credentials: "include" });
    return { status: res.status, body: (await res.json()) as { data: { expiresIn: number } } };
  });
  expect(refreshed.status).toBe(200);
  expect(refreshed.body.data.expiresIn).toBe(900);
  const rotated = await refreshCookie();
  expect(rotated!.value).not.toBe(before);
  expect(rotated!.httpOnly).toBe(true);
  expect(rotated!.sameSite).toBe("Lax");
  expect(rotated!.path).toBe("/api/auth");

  // 6. Logout clears the cookie and revokes the family (replay → 401).
  const preLogout = rotated!.value;
  const logout = await page.evaluate(async () => {
    const res = await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
    return res.status;
  });
  expect(logout).toBe(200);
  expect(await refreshCookie()).toBeFalsy();

  // Re-inject the revoked cookie and confirm the family no longer authenticates.
  await context.addCookies([
    {
      name: REFRESH_COOKIE,
      value: preLogout,
      domain: "localhost",
      path: "/api/auth",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
  const replay = await page.evaluate(async () => {
    const res = await fetch("/api/auth/refresh", { method: "POST", credentials: "include" });
    return res.status;
  });
  expect(replay).toBe(401);
});
