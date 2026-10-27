import { expect, test } from "@playwright/test";

import { loginViaSso } from "../helpers/api";

// P0 journey §31.2.1: first login lands on an empty user workspace. The seeded
// tenant (acme.com) has no agents or conversations, so every aggregate section
// renders its empty state rather than a blank screen.
test("first login shows the empty-state user workspace", async ({ page }) => {
  await loginViaSso(page);

  await expect(page.getByRole("heading", { name: "用户工作区" })).toBeVisible();
  await expect(page.getByText("alice@acme.com")).toBeVisible();

  // No agent is published in the seeded tenant: the recommendation section
  // states that fact, while the conversation section keeps its own empty state.
  await expect(page.getByText("您的管理员尚未发布智能体")).toBeVisible();
  await expect(page.getByText("暂无会话")).toBeVisible();
});
