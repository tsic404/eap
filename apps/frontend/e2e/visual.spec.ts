import { expect, test } from "@playwright/test";

import { loginViaSso } from "./helpers/api";

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
  await page.goto("/login");
  await expect(page.getByRole("button", { name: "SSO 登录" })).toBeVisible();
  await expect(page).toHaveScreenshot("login.png", SCREENSHOT_OPTIONS);
});

test("forbidden page matches the baseline", async ({ page }) => {
  await page.goto("/forbidden");
  await expect(page.getByRole("heading", { name: "无管理权限" })).toBeVisible();
  await expect(page).toHaveScreenshot("forbidden.png", SCREENSHOT_OPTIONS);
});

test("empty user workspace matches the baseline", async ({ page }) => {
  await loginViaSso(page);
  await expect(page.getByText("暂无推荐")).toBeVisible();
  await expect(page).toHaveScreenshot("user-empty.png", SCREENSHOT_OPTIONS);
});

test("empty agent marketplace matches the baseline", async ({ page }) => {
  await loginViaSso(page);
  await page.goto("/user/agents");
  await expect(page.getByText("暂无已发布的智能体")).toBeVisible();
  await expect(page).toHaveScreenshot("agents-empty.png", SCREENSHOT_OPTIONS);
});

test("empty conversation list matches the baseline", async ({ page }) => {
  await loginViaSso(page);
  await page.goto("/user/conversations");
  await expect(page.getByText("暂无会话")).toBeVisible();
  await expect(page).toHaveScreenshot("conversations-empty.png", SCREENSHOT_OPTIONS);
});
