import { expect, test } from "@playwright/test";

import { loginViaSso } from "../helpers/api";

// P0 journey §31.2.1: first login lands on an empty user workspace. The seeded
// tenant (acme.com) has no agents or conversations, so every aggregate section
// renders its empty state rather than a blank screen.
test("first login shows the empty-state user workspace", async ({ page }) => {
  await loginViaSso(page);

  await expect(page.getByRole("heading", { name: "用户工作区" })).toBeVisible();
  await expect(page.getByText("alice@acme.com")).toBeVisible();

  // Recommended agents and recent conversations are both empty.
  await expect(page.getByText("暂无推荐")).toBeVisible();
  await expect(page.getByText("暂无会话")).toBeVisible();
});
