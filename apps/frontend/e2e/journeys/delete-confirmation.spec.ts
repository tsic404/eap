import { expect, test } from "@playwright/test";

import { envelope, loginViaSso, mockConversation } from "../helpers/api";

// P0 journey §31.2.6: destructive actions require a second confirmation before
// the DELETE is issued.
test("deleting a conversation requires an explicit confirmation", async ({ page }) => {
  await loginViaSso(page);

  await page.route("**/api/conversations*", (route) =>
    route.fulfill({
      json: envelope({ items: [mockConversation({ title: "项目讨论" })], nextCursor: null }),
    }),
  );

  let deleteFired = false;
  await page.route("**/api/conversations/conv-1", (route) => {
    if (route.request().method() === "DELETE") {
      deleteFired = true;
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill({ json: envelope(mockConversation({ title: "项目讨论" })) });
  });

  await page.goto("/user/conversations");
  await expect(page.getByText("项目讨论")).toBeVisible();

  // The trash button opens the confirmation dialog; no DELETE has been sent yet.
  await page.getByRole("button", { name: "删除会话" }).click();
  await expect(page.getByRole("dialog", { name: "删除会话" })).toBeVisible();
  await expect(page.getByText(/确定要删除/)).toBeVisible();
  expect(deleteFired).toBe(false);

  // Confirming issues the DELETE and reports success.
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page.getByText("会话已删除")).toBeVisible();
  expect(deleteFired).toBe(true);
});
