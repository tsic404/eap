import { expect, test } from "@playwright/test";

import { envelope } from "../helpers/api";

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
