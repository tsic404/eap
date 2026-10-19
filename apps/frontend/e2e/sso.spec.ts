import { expect, test } from "@playwright/test";

import { loginViaSso } from "./helpers/api";

// SSO end-to-end regression (UC-01-1 / UC-01-4).
//
// A real browser completes the OIDC authorization-code + PKCE flow against a
// mock IdP, lands on /user, and renders the authenticated profile. The refresh
// cookie contract (HttpOnly, path=/api/auth/refresh, SameSite=Lax, 30-day
// Max-Age) and the rotation / logout behaviour are asserted against the
// browser's observable cookie store.
//
// Note: the access-token TTL is `expiresIn: 900` in the refresh response BODY;
// the refresh cookie itself carries the 30-day refresh TTL (2592000s), not 900.

const REFRESH_COOKIE = "refresh_token";
const REFRESH_TTL_SECONDS = 2592000;
/** A JWT is three dot-separated segments; matched by shape, not by cookie name. */
const JWT_SHAPE = /[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/;

test("SSO login lands on /user and honours the refresh-cookie contract", async ({
  page,
  context,
}) => {
  // 1. A browser that logged in before the credential path narrowed still holds
  // a cookie under the wider path. Two same-named cookies both reach the refresh
  // endpoint, and the wider path is sent last, so the stale one would shadow
  // every rotation; the login response must retire it.
  await context.addCookies([
    {
      name: REFRESH_COOKIE,
      value: "pre-confinement",
      domain: "localhost",
      path: "/api/auth",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);

  // 2. Start the SSO login from the frontend login page.
  await page.goto("/login");
  await page.getByRole("button", { name: "SSO 登录" }).click();

  // 3. The browser follows login → IdP authorize → callback → /user.
  await page.waitForURL("**/user");

  // 4. The frontend renders the authenticated profile from /api/me. The name
  // appears in both the header and the profile card, so target the unique
  // email and the first name match.
  await expect(page.getByText("alice@acme.com")).toBeVisible();
  await expect(page.getByText("Alice").first()).toBeVisible();

  // 5. The planted wider-path cookie is gone: exactly one refresh cookie
  // remains, confined to the refresh endpoint.
  const cookiesAfterLogin = (await context.cookies()).filter(
    (c) => c.name === REFRESH_COOKIE,
  );
  expect(cookiesAfterLogin).toHaveLength(1);
  expect(cookiesAfterLogin[0].path).toBe("/api/auth/refresh");

  // 6. Refresh cookie attributes (rotated once by the frontend bootstrap).
  const refreshCookie = async () =>
    (await context.cookies()).find((c) => c.name === REFRESH_COOKIE);
  const initial = await refreshCookie();
  expect(initial).toBeTruthy();
  expect(initial!.httpOnly).toBe(true);
  expect(initial!.sameSite).toBe("Lax");
  expect(initial!.path).toBe("/api/auth/refresh");
  const ttl = initial!.expires - Math.floor(Date.now() / 1000);
  expect(Math.abs(ttl - REFRESH_TTL_SECONDS)).toBeLessThan(60);

  // 7. Refresh rotates the cookie and returns expiresIn: 900 in the body.
  const before = initial!.value;
  const refreshed = await page.evaluate(async () => {
    const res = await fetch("/api/auth/refresh", { method: "POST", credentials: "include" });
    return {
      status: res.status,
      body: (await res.json()) as { data: { accessToken: string; expiresIn: number } },
    };
  });
  expect(refreshed.status).toBe(200);
  expect(refreshed.body.data.expiresIn).toBe(900);
  const rotated = await refreshCookie();
  expect(rotated!.value).not.toBe(before);
  expect(rotated!.httpOnly).toBe(true);
  expect(rotated!.sameSite).toBe("Lax");
  expect(rotated!.path).toBe("/api/auth/refresh");

  // 8. Logout ends the session server-side. The app sends the access token,
  // which names the session (the refresh family), so revocation does not need
  // the browser to submit a credential C-3 confines to the refresh endpoint.
  // Re-inject the pre-logout credential and pin that it no longer authenticates.
  const preLogout = rotated!.value;
  const logout = await page.evaluate(async (token) => {
    const res = await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "include",
      headers: { Authorization: `Bearer ${token}` },
    });
    return res.status;
  }, refreshed.body.data.accessToken);
  expect(logout).toBe(200);
  expect(await refreshCookie()).toBeFalsy();

  await context.addCookies([
    {
      name: REFRESH_COOKIE,
      value: preLogout,
      domain: "localhost",
      path: "/api/auth/refresh",
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

test("the access token stays out of script-readable storage and follows the session across tabs", async ({
  page,
  context,
}) => {
  await loginViaSso(page);

  // 1. Nothing a page script can read carries the access token (F4 / C-2).
  const readable = await page.evaluate(() => ({
    cookie: document.cookie,
    local: JSON.stringify(localStorage),
    session: JSON.stringify(sessionStorage),
  }));
  expect(readable.cookie).not.toContain("eap_access_token");
  expect(`${readable.cookie} ${readable.local} ${readable.session}`).not.toMatch(JWT_SHAPE);

  // 2. A second tab holds no token of its own, so it re-enters the refresh
  //    channel (the shared HttpOnly cookie) and reaches the workspace (C-3).
  const secondTab = await context.newPage();
  await secondTab.goto("/user");
  await expect(secondTab.getByText("alice@acme.com")).toBeVisible();

  // 3. Signing out in one tab ends the session in the other.
  await page.getByTitle("Alice").click();
  await page.getByRole("menuitem", { name: "登出" }).click();
  await page.waitForURL("**/login");
  await secondTab.waitForURL("**/login");
});
