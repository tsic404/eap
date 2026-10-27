import { expect, test } from "@playwright/test";

import { envelope, loginViaSso } from "./helpers/api";

// Visual regression (§33.7.5): five stable, deterministic UI states. Baselines
// live under `e2e/visual.spec.ts-snapshots/` (Playwright's default per-spec
// snapshot dir); a run fails when more than 1% of pixels differ from the
// committed baseline.
test.use({ viewport: { width: 1280, height: 720 } });

const SCREENSHOT_OPTIONS = {
  animations: "disabled",
  maxDiffPixelRatio: 0.01,
  caret: "hide",
} as const;

test("login page matches the baseline", async ({ page }) => {
  // The harness stack declares the capability on; the baseline pins the
  // deployment default (closed), so the read point is answered as closed here.
  await page.route("**/api/auth/capabilities", (route) =>
    route.fulfill({ json: envelope({ localLoginEnabled: false }) }),
  );
  await page.goto("/login");
  await expect(page.getByRole("button", { name: "SSO 登录" })).toBeVisible();
  await expect(page).toHaveScreenshot("login.png", SCREENSHOT_OPTIONS);
});

test("forbidden page matches the baseline", async ({ page }) => {
  await page.goto("/forbidden");
  await expect(page.getByRole("heading", { name: "无管理权限" })).toBeVisible();
  await expect(page).toHaveScreenshot("forbidden.png", SCREENSHOT_OPTIONS);
});

// Both empty-state cases pin the guidance card, not the whole page: the card
// is under 1% of the viewport, so a page-wide budget would pass a revert to the
// pre-guidance placeholder, while the region locator fails outright.
test("empty user workspace matches the baseline", async ({ page }) => {
  await loginViaSso(page);
  const guidance = page.getByRole("region", { name: "您的管理员尚未发布智能体" });
  await expect(guidance).toBeVisible();
  await expect(guidance).toHaveScreenshot("user-empty.png", SCREENSHOT_OPTIONS);
});

test("empty agent marketplace matches the baseline", async ({ page }) => {
  await loginViaSso(page);
  await page.goto("/user/agents");
  const guidance = page.getByRole("region", { name: "您的管理员尚未发布智能体" });
  await expect(guidance).toBeVisible();
  await expect(guidance).toHaveScreenshot("agents-empty.png", SCREENSHOT_OPTIONS);
});

test("empty conversation list matches the baseline", async ({ page }) => {
  await loginViaSso(page);
  await page.goto("/user/conversations");
  await expect(page.getByText("暂无会话")).toBeVisible();
  await expect(page).toHaveScreenshot("conversations-empty.png", SCREENSHOT_OPTIONS);
});
