import { expect, test, type Page } from "@playwright/test";

import type { Conversation } from "@/lib/conversation-types";
import { formatDateTime } from "@/lib/formatters";

import {
  envelope,
  loginViaSso,
  messageEndFrame,
  mockAgent,
  mockConversation,
  sseFrame,
} from "../helpers/api";

// TC-conv-03 (AC-24): 会话列表条目要素齐全、继续会话上下文无丢失、新建会话
// 打开空白聊天窗口。判据语义见 `docs/03-acceptance.md`；会话按用户归属、
// 尚无回合时历史为空页（C-53、C-54）。

const COMPOSER_PLACEHOLDER = "输入消息，Enter 发送，Shift+Enter 换行";

/** A newest-first history page with both turns; `historyToChatMessages` reorders it. */
const HISTORY = {
  items: [
    {
      id: "turn-2",
      query: "第二轮问题",
      answer: "第二轮回答",
      status: "normal",
      feedback: null,
      files: [],
      createdAt: "2026-01-02T00:00:00Z",
    },
    {
      id: "turn-1",
      query: "第一轮问题",
      answer: "第一轮回答",
      status: "normal",
      feedback: null,
      files: [],
      createdAt: "2026-01-01T00:00:00Z",
    },
  ],
  nextCursor: null,
};

/** Stub the list page's `GET /api/conversations` with the given conversations. */
async function mockConversationList(page: Page, items: Conversation[]): Promise<void> {
  await page.route("**/api/conversations*", (route) =>
    route.fulfill({ json: envelope({ items, nextCursor: null }) }),
  );
}

test("TC-conv-03 list: 会话列表条目要素齐全", async ({ page }) => {
  await loginViaSso(page);

  await mockConversationList(page, [
    mockConversation({
      id: "conv-1",
      title: "项目讨论",
      agentName: "测试智能体",
      updatedAt: "2026-01-02T03:04:00Z",
    }),
    mockConversation({
      id: "conv-2",
      title: "需求评审",
      agentName: "评审助手",
      updatedAt: "2026-02-03T04:05:00Z",
    }),
  ]);

  await page.goto("/user/conversations");

  // Each entry carries the conversation title, its agent name and the update
  // time (C-53), rendered as `<agent> · <formatted updatedAt>`. The expected
  // string comes from the app's own formatter, so a row rendering createdAt
  // instead of updatedAt (2026-01-01T00:00:00Z) fails.
  for (const [title, agentName, updatedAt] of [
    ["项目讨论", "测试智能体", "2026-01-02T03:04:00Z"],
    ["需求评审", "评审助手", "2026-02-03T04:05:00Z"],
  ] as const) {
    await expect(page.getByRole("link", { name: new RegExp(title) })).toBeVisible();
    await expect(
      page.getByText(`${agentName} · ${formatDateTime(updatedAt)}`, { exact: true }),
    ).toBeVisible();
  }
});

test("TC-conv-03 resume: 继续会话上下文无丢失", async ({ page }) => {
  await loginViaSso(page);

  await page.route("**/api/conversations/conv-1", (route) =>
    route.fulfill({ json: envelope(mockConversation({ title: "项目讨论" })) }),
  );
  await page.route("**/api/conversations/conv-1/messages*", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body:
          sseFrame("message", { content: "续接后的回答", messageId: "msg-live" }) +
          messageEndFrame("trace-live"),
      });
      return;
    }
    await route.fulfill({ json: envelope(HISTORY) });
  });

  await page.goto("/user/conversations/conv-1");
  await expect(page.getByText("测试智能体")).toBeVisible();

  // Both turns of the transcript are restored, and they render in turn order.
  const firstQuestion = page.getByText("第一轮问题");
  const secondQuestion = page.getByText("第二轮问题");
  await expect(firstQuestion).toBeVisible();
  await expect(page.getByText("第一轮回答")).toBeVisible();
  await expect(secondQuestion).toBeVisible();
  await expect(page.getByText("第二轮回答")).toBeVisible();
  expect((await firstQuestion.boundingBox())!.y).toBeLessThan(
    (await secondQuestion.boundingBox())!.y,
  );

  // The restored conversation stays continuable: a new turn is appended while
  // the history above it remains on screen.
  await page.getByPlaceholder(COMPOSER_PLACEHOLDER).fill("接着问");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("续接后的回答")).toBeVisible();
  await expect(page.getByText("第一轮问题")).toBeVisible();
});

test("TC-conv-03 new: 经入口新建会话打开空白聊天窗口", async ({ page }) => {
  await loginViaSso(page);

  // The tenant has one published agent and one draft: only the published one
  // may start a conversation (C-34 / C-53), so the draft must not be offered.
  await page.route("**/api/agents*", (route) =>
    route.fulfill({
      json: envelope({
        items: [
          mockAgent({ agentId: "ag-1", name: "测试智能体", status: "published" }),
          mockAgent({ agentId: "ag-draft", name: "草稿智能体", status: "draft" }),
        ],
        total: 2,
        page: 1,
        pageSize: 100,
      }),
    }),
  );

  // A conversation created but never used has no turns yet, so its history is
  // an empty page (C-54) and the chat window opens blank.
  const fresh = mockConversation({ id: "conv-new", title: null });
  let created = false;
  let createdWith: Record<string, unknown> | null = null;
  await page.route("**/api/conversations*", async (route) => {
    const request = route.request();
    if (new URL(request.url()).pathname.endsWith("/api/conversations")) {
      if (request.method() === "POST") {
        createdWith = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
        created = true;
        await route.fulfill({ status: 201, json: envelope(fresh) });
        return;
      }
      await route.fulfill({
        json: envelope({ items: created ? [fresh] : [], nextCursor: null }),
      });
      return;
    }
    await route.continue();
  });
  await page.route("**/api/conversations/conv-new/messages*", (route) =>
    route.fulfill({ json: envelope({ items: [], nextCursor: null }) }),
  );
  await page.route("**/api/conversations/conv-new", (route) =>
    route.fulfill({ json: envelope(fresh) }),
  );

  await page.goto("/user/conversations");

  // The entry is present and operable even with no conversation at all, and it
  // is the only way the empty list can gain one.
  const entry = page.getByRole("button", { name: "新建会话" });
  await expect(entry).toBeVisible();
  await expect(entry).toBeEnabled();
  await entry.click();

  // The picker offers published agents only.
  const picker = page.getByRole("dialog", { name: "新建会话" });
  await expect(picker).toBeVisible();
  await expect(picker.getByRole("button", { name: /测试智能体/ })).toBeVisible();
  await expect(picker.getByText("草稿智能体")).toHaveCount(0);

  await picker.getByRole("button", { name: /测试智能体/ }).click();
  await page.waitForURL("**/user/conversations/conv-new");
  expect(createdWith).toMatchObject({ agentId: "ag-1" });

  // The header carries the agent chosen in the picker, so a details response
  // without `agentName` cannot silently fall back to the default label.
  await expect(page.getByText("测试智能体")).toBeVisible();

  // The blank window offers the start prompt and an enabled composer; the
  // empty state itself means the transcript is empty.
  await expect(page.getByText("开始对话")).toBeVisible();
  await expect(page.getByText("在下方输入消息，向智能体提问")).toBeVisible();
  await expect(page.getByPlaceholder(COMPOSER_PLACEHOLDER)).toBeEnabled();

  // A fresh load of the list shows the new conversation with its agent and the
  // untitled placeholder, and the entry stays operable next to it.
  await page.goto("/user/conversations");
  await expect(page.getByText("未命名会话")).toBeVisible();
  await expect(page.getByText("测试智能体 ·", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "新建会话" })).toBeEnabled();
});

test("TC-conv-03 new: 创建失败停留原页并给出可关闭的错误提示", async ({ page }) => {
  await loginViaSso(page);

  await page.route("**/api/agents*", (route) =>
    route.fulfill({
      json: envelope({
        items: [mockAgent({ agentId: "ag-1", name: "测试智能体" })],
        total: 1,
        page: 1,
        pageSize: 100,
      }),
    }),
  );

  let posts = 0;
  await page.route("**/api/conversations*", async (route) => {
    const request = route.request();
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname.endsWith("/api/conversations")
    ) {
      posts += 1;
      await route.fulfill({
        status: 500,
        json: { error: { code: "INTERNAL_ERROR", message: "boom" } },
      });
      return;
    }
    await route.fulfill({ json: envelope({ items: [], nextCursor: null }) });
  });

  await page.goto("/user/conversations");
  await page.getByRole("button", { name: "新建会话" }).click();
  await page
    .getByRole("dialog", { name: "新建会话" })
    .getByRole("button", { name: /测试智能体/ })
    .click();

  // Failure keeps the user on the list page, creates nothing locally, and the
  // prompt inside the picker is closable on its own.
  const dialog = page.getByRole("dialog", { name: "新建会话" });
  const prompt = dialog.getByRole("alert").filter({ hasText: "新建会话失败，请重试" });
  await expect(prompt).toBeVisible();
  expect(posts).toBe(1);
  await expect(page).toHaveURL(/\/user\/conversations$/);
  await expect(page.getByText("开始对话", { exact: true })).toHaveCount(0);

  await prompt.getByRole("button", { name: "关闭错误提示" }).click();
  await expect(prompt).toHaveCount(0);

  // The picker stays open so another attempt is possible.
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: /测试智能体/ })).toBeEnabled();
});
