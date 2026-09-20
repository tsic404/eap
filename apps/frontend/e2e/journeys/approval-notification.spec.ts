import { expect, test } from "@playwright/test";

import { envelope, loginViaSso, PROXY_ORIGIN } from "../helpers/api";

const TASK = {
  id: "task-1",
  tenant_id: "tenant-1",
  creator_id: "user-1",
  assignee_id: null,
  type: "tool_approval",
  title: "等待审批：接入搜索工具",
  priority: "high",
  status: "pending",
  payload: {},
  result: null,
  error_message: null,
  retry_count: 0,
  max_retries: 3,
  expires_at: null,
  created_at: "2026-01-01T00:00:00Z",
  resolved_at: null,
};

// P0 journey §31.2.5: pending approvals surface a count badge, and an
// admin-capable role can approve directly from the task card.
test("pending approvals show a count badge and approve action", async ({ page, context }) => {
  await context.addCookies([
    { name: "eap_role", value: "agent_admin", url: PROXY_ORIGIN },
  ]);
  await loginViaSso(page);

  await page.route("**/api/tasks**", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        json: envelope({ items: [TASK], next_cursor: null }),
      });
      return;
    }
    await route.continue();
  });

  await page.goto("/user/tasks");

  await expect(page.getByText("待审批 1")).toBeVisible();
  await expect(page.getByText("等待审批：接入搜索工具")).toBeVisible();

  // Approving POSTs to the approve endpoint and confirms via toast.
  await page.route("**/api/tasks/task-1/approve", (route) =>
    route.fulfill({ json: envelope({ ...TASK, status: "approved" }) }),
  );
  await page.getByRole("button", { name: "同意" }).click();
  await expect(page.getByText("已通过")).toBeVisible();
});
