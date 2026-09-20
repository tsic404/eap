import { expect, test } from "@playwright/test";

import { envelope, loginViaSso, messageEndFrame, mockConversation } from "../helpers/api";

// P0 journey §31.2.3: a conversation that overruns the model's context window
// surfaces the token-truncation notice (from `message_end` metadata).
test("long conversation surfaces the token-truncation notice", async ({ page }) => {
  await loginViaSso(page);

  await page.route("**/api/conversations/conv-1", (route) =>
    route.fulfill({ json: envelope(mockConversation({ title: "长对话" })) }),
  );
  await page.route("**/api/conversations/conv-1/messages", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: messageEndFrame("trace-1", { truncationNotice: true }),
      });
      return;
    }
    await route.fulfill({ json: envelope({ items: [], nextCursor: null }) });
  });

  await page.goto("/user/conversations/conv-1");
  await expect(page.getByText("测试智能体")).toBeVisible();

  await page.getByPlaceholder("输入消息，Enter 发送，Shift+Enter 换行").fill("继续之前的话题");
  await page.getByRole("button", { name: "发送" }).click();

  await expect(page.getByText("对话较长，较早的消息可能未被纳入上下文")).toBeVisible();
});
