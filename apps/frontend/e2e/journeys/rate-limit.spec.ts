import { expect, test } from "@playwright/test";

import { envelope, loginViaSso, mockConversation } from "../helpers/api";

const RATE_LIMITED_BODY = JSON.stringify({
  error: { code: "RATE_LIMITED", message: "每分钟最多 20 条" },
});

const QUOTA_EXCEEDED_BODY = JSON.stringify({
  error: { code: "QUOTA_EXCEEDED", message: "quota exhausted" },
});

/**
 * Mock the conversation detail + an empty history, and answer the next message
 * POST with a 429 error body. Registered as a single route so the history GET
 * and the message POST both resolve deterministically.
 */
async function mockConversationWith429(
  page: import("@playwright/test").Page,
  errorBody: string,
): Promise<void> {
  await page.route("**/api/conversations/conv-1", (route) =>
    route.fulfill({ json: envelope(mockConversation()) }),
  );
  await page.route("**/api/conversations/conv-1/messages", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 429,
        contentType: "application/json",
        body: errorBody,
      });
      return;
    }
    await route.fulfill({ json: envelope({ items: [], nextCursor: null }) });
  });
  await page.goto("/user/conversations/conv-1");
  await expect(page.getByText("测试智能体")).toBeVisible();
}

// P0 journey §31.2.4: a rate-limited send (429) disables the composer and shows
// a countdown placeholder so the user understands when they can retry.
test("rate-limited send enters a cooldown state", async ({ page }) => {
  await loginViaSso(page);
  await mockConversationWith429(page, RATE_LIMITED_BODY);

  const input = page.getByPlaceholder("输入消息，Enter 发送，Shift+Enter 换行");
  await input.fill("你好");
  await page.getByRole("button", { name: "发送" }).click();

  await expect(page.getByText("消息发送太频繁，请稍后再试（每分钟最多 20 条）")).toBeVisible();
  // The composer's placeholder flips to the countdown and the field disables.
  const cooldownInput = page.getByPlaceholder(/秒后可发送/);
  await expect(cooldownInput).toBeVisible();
  await expect(cooldownInput).toBeDisabled();
});

// QUOTA_EXCEEDED is terminal (not a cooldown): the toast explains the account is
// out of quota rather than starting a retry countdown.
test("quota-exceeded send surfaces an explanatory toast", async ({ page }) => {
  await loginViaSso(page);
  await mockConversationWith429(page, QUOTA_EXCEEDED_BODY);

  await page.getByPlaceholder("输入消息，Enter 发送，Shift+Enter 换行").fill("你好");
  await page.getByRole("button", { name: "发送" }).click();

  await expect(page.getByText("本月 API 调用量已用完，请联系管理员升级")).toBeVisible();
});
