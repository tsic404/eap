import { expect, test } from "@playwright/test";

import { envelope, loginViaSso, mockConversation } from "../helpers/api";

// TC-conv-03 (AC-24): a deleted conversation stays recoverable from the UI —
// the success prompt offers the undo and taking it puts the conversation back
// into the list (C-104).
test("undoing the deletion restores the conversation to the list", async ({ page }) => {
  await loginViaSso(page);

  let deleted = false;
  let restoreFired = false;

  await page.route("**/api/conversations**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/restore")) {
      restoreFired = true;
      deleted = false;
      return route.fulfill({ json: envelope(mockConversation()) });
    }
    if (path === "/api/conversations/conv-1" && route.request().method() === "DELETE") {
      deleted = true;
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill({
      json: envelope({
        items: deleted ? [] : [mockConversation({ title: "项目讨论" })],
        nextCursor: null,
      }),
    });
  });

  await page.goto("/user/conversations");
  await expect(page.getByText("项目讨论")).toBeVisible();

  // The confirmation states the deletion is undoable rather than final.
  await page.getByRole("button", { name: "删除会话" }).click();
  const dialog = page.getByRole("dialog", { name: "删除会话" });
  await expect(dialog).toContainText("撤销");

  await page.getByRole("button", { name: "删除", exact: true }).click();

  // The result prompt carries the undo; nothing has been restored yet.
  await expect(page.getByText("会话已删除")).toBeVisible();
  const undo = page.getByRole("button", { name: "撤销" });
  await expect(undo).toBeVisible();
  expect(restoreFired).toBe(false);
  await expect(page.getByText("项目讨论")).toBeHidden();

  await undo.click();

  await expect(page.getByText("项目讨论")).toBeVisible();
  expect(restoreFired).toBe(true);
});
