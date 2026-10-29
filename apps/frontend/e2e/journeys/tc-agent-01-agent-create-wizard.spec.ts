import { expect, test, type Page } from "@playwright/test";

import type { AgentDetail } from "@/lib/agent-types";

import { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";
import { envelope } from "../helpers/api";

// TC-agent-01 (AC-08) browser evidence: the creation wizard walks its four
// steps, the preview repeats every entered value, and the create response is
// what moves the browser to the detail page.
//
// The QA stack ships no Dify console, so the real `POST /api/agents` answers
// 502 before it can reach 201. The create call is therefore answered at the
// browser edge here; the wizard, the preview and "201 navigates, failure does
// not" are the UI's contract, while the backend's own 201 belongs to the
// agent-service acceptance suite.

const TC_ID = "TC-agent-01";

const STEP_COUNT = 4;

const TOOLS = [
  {
    tool_id: "tool-1",
    name: "天气查询",
    description: "按城市查询天气",
    type: "http",
    status: "active",
  },
  {
    tool_id: "tool-2",
    name: "汇率换算",
    description: "按币种换算金额",
    type: "http",
    status: "active",
  },
];

const CREATED_AGENT: AgentDetail = {
  agentId: "travel-bot",
  tenantId: "tenant-1",
  difyAppId: "dify-app-1",
  name: "差旅助手",
  description: "处理差旅申请",
  type: "agent",
  category: "行政",
  icon: "🧳",
  tags: ["差旅", "审批"],
  status: "draft",
  modelId: "gpt-4o-mini",
  modelName: "GPT-4o mini",
  modelProvider: "openai",
  prompt: "你是差旅助手。",
  version: 1,
  createdBy: "user-1",
  publishedAt: null,
  publishedBy: null,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
  boundKnowledge: [],
  boundTools: [
    { toolId: "tool-1", name: "天气查询" },
    { toolId: "tool-2", name: "汇率换算" },
  ],
  recentLogs: [],
  conversationCount: 0,
};

/** The indicator's four entries; only the current one carries the emphasis weight. */
async function expectHighlightedStep(page: Page, current: number): Promise<void> {
  const items = page.getByRole("list", { name: "创建步骤" }).locator("li");
  await expect(items).toHaveCount(STEP_COUNT);
  for (let index = 0; index < STEP_COUNT; index += 1) {
    const label = items.nth(index).locator("span").nth(1);
    const weight = await label.evaluate((node) => getComputedStyle(node).fontWeight);
    expect(weight, `step ${index + 1} emphasis`).toBe(index === current ? "500" : "400");
    // Completed steps replace their ordinal with a check mark.
    await expect(items.nth(index).locator("svg")).toHaveCount(index < current ? 1 : 0);
  }
}

/** The value cell beside a preview row's label. */
function previewValue(page: Page, label: string) {
  return page.getByText(label, { exact: true }).locator("xpath=following-sibling::span[1]");
}

async function signInAsAgentAdmin(page: Page): Promise<void> {
  const admin = LOCAL_ACCOUNTS.admin;
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(admin.email);
  await page.getByLabel("口令").fill(LOCAL_ACCOUNT_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/user");
  // The auth bootstrap writes the backend role into the role cookie the admin
  // routes read; wait for it before navigating there.
  await expect(page.getByText(admin.email)).toBeVisible();
}

test(`${TC_ID} wizard: 表单四步逐步推进且步骤指示器高亮当前步`, async ({ page }) => {
  await page.route("**/api/tools", (route) => route.fulfill({ json: envelope(TOOLS) }));
  await signInAsAgentAdmin(page);

  await page.goto("/admin/agents/new");

  // Step 1 — basic info only.
  await expectHighlightedStep(page, 0);
  await expect(page.getByLabel("标识（agentId）")).toBeVisible();
  await expect(page.getByLabel("名称")).toBeVisible();
  await expect(page.getByLabel("模型提供方")).toHaveCount(0);

  await page.getByLabel("标识（agentId）").fill("travel-bot");
  await page.getByLabel("名称").fill("差旅助手");
  await page.getByRole("button", { name: "下一步" }).click();

  // Step 2 — the model and prompt fields replace the basic ones.
  await expectHighlightedStep(page, 1);
  await expect(page.getByLabel("模型提供方")).toBeVisible();
  await expect(page.getByLabel("系统 Prompt")).toBeVisible();
  await expect(page.getByLabel("标识（agentId）")).toHaveCount(0);

  // Back and forth keeps the entered values and the indicator in step.
  await page.getByRole("button", { name: "上一步" }).click();
  await expectHighlightedStep(page, 0);
  await expect(page.getByLabel("名称")).toHaveValue("差旅助手");
  await page.getByRole("button", { name: "下一步" }).click();
  await expectHighlightedStep(page, 1);

  await page.getByLabel("模型提供方").fill("openai");
  await page.getByLabel("模型 ID").fill("gpt-4o-mini");
  await page.getByRole("button", { name: "下一步" }).click();

  // Step 3 — resource binding.
  await expectHighlightedStep(page, 2);
  await expect(page.getByRole("heading", { name: "绑定工具" })).toBeVisible();

  await page.getByText("天气查询", { exact: true }).click();
  await page.getByText("汇率换算", { exact: true }).click();
  await page.getByRole("button", { name: "下一步" }).click();

  // Step 4 — the preview.
  await expectHighlightedStep(page, 3);
  await expect(page.getByRole("button", { name: "创建智能体" })).toBeVisible();
});

test(`${TC_ID} wizard: 双击「下一步」的第二击不创建智能体`, async ({ page }) => {
  const posts: unknown[] = [];
  await page.route("**/api/tools", (route) => route.fulfill({ json: envelope(TOOLS) }));
  await page.route("**/api/agents", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    posts.push(route.request().postDataJSON());
    await route.fulfill({ status: 201, json: envelope({ ...CREATED_AGENT }) });
  });
  await page.route("**/api/agents/travel-bot", (route) =>
    route.fulfill({ json: envelope(CREATED_AGENT) }),
  );

  await signInAsAgentAdmin(page);
  await page.goto("/admin/agents/new");
  await page.getByLabel("标识（agentId）").fill("travel-bot");
  await page.getByLabel("名称").fill("差旅助手");
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByLabel("模型提供方").fill("openai");
  await page.getByLabel("模型 ID").fill("gpt-4o-mini");
  await page.getByRole("button", { name: "下一步" }).click();

  await expectHighlightedStep(page, 2);
  // A real double click at the advance button's position: the second click of
  // the gesture must never create the agent.
  const nextBox = await page.getByRole("button", { name: "下一步" }).boundingBox();
  if (!nextBox) throw new Error("下一步 has no layout box");
  await page.mouse.dblclick(nextBox.x + nextBox.width / 2, nextBox.y + nextBox.height / 2);

  await expectHighlightedStep(page, 3);
  const createButton = page.getByRole("button", { name: "创建智能体" });
  await expect(createButton).toBeVisible();
  await expect(createButton).toBeEnabled();
  await expect(page).toHaveURL(/\/admin\/agents\/new$/);
  // The only request this gesture may produce: a created second click reads 2.
  await createButton.click();
  await expect.poll(() => posts.length).toBe(1);
});

test(`${TC_ID} preview: 预览区与已填配置逐项一致`, async ({ page }) => {
  await page.route("**/api/tools", (route) => route.fulfill({ json: envelope(TOOLS) }));
  await signInAsAgentAdmin(page);

  await page.goto("/admin/agents/new");
  await page.getByLabel("标识（agentId）").fill("travel-bot");
  await page.getByLabel("名称").fill("差旅助手");
  await page.getByLabel("描述").fill("处理差旅申请");
  await page.getByLabel("类型").selectOption("agent");
  await page.getByLabel("分类").fill("行政");
  await page.getByLabel("图标").fill("🧳");
  await page.getByLabel("标签").fill("差旅, 审批");
  await page.getByRole("button", { name: "下一步" }).click();

  await page.getByLabel("模型提供方").fill("openai");
  await page.getByLabel("模型 ID").fill("gpt-4o-mini");
  await page.getByLabel("模型名称").fill("GPT-4o mini");
  await page.getByLabel("系统 Prompt").fill("你是差旅助手。");
  await page.getByRole("button", { name: "下一步" }).click();

  await page.getByText("天气查询", { exact: true }).click();
  await page.getByText("汇率换算", { exact: true }).click();
  await page.getByRole("button", { name: "下一步" }).click();

  // Every entered value is repeated, item by item.
  await expect(previewValue(page, "标识")).toHaveText("travel-bot");
  await expect(previewValue(page, "名称")).toHaveText("差旅助手");
  await expect(previewValue(page, "类型")).toHaveText("智能体");
  await expect(previewValue(page, "描述")).toHaveText("处理差旅申请");
  await expect(previewValue(page, "分类")).toHaveText("行政");
  await expect(previewValue(page, "图标")).toHaveText("🧳");
  await expect(page.getByText("差旅", { exact: true })).toBeVisible();
  await expect(page.getByText("审批", { exact: true })).toBeVisible();
  await expect(previewValue(page, "模型")).toHaveText("openai / gpt-4o-mini");
  await expect(previewValue(page, "绑定工具")).toHaveText("2 个工具");
});

test(`${TC_ID} submit: 创建返回 201 后显示成功并跳转详情页`, async ({ page }) => {
  const posts: unknown[] = [];
  await page.route("**/api/tools", (route) => route.fulfill({ json: envelope(TOOLS) }));
  await page.route("**/api/agents", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    posts.push(route.request().postDataJSON());
    await route.fulfill({
      status: 201,
      json: envelope({ ...CREATED_AGENT }),
    });
  });
  await page.route("**/api/agents/travel-bot", (route) =>
    route.fulfill({ json: envelope(CREATED_AGENT) }),
  );

  await signInAsAgentAdmin(page);
  await page.goto("/admin/agents/new");
  await page.getByLabel("标识（agentId）").fill("travel-bot");
  await page.getByLabel("名称").fill("差旅助手");
  await page.getByLabel("描述").fill("处理差旅申请");
  await page.getByLabel("类型").selectOption("agent");
  await page.getByLabel("分类").fill("行政");
  await page.getByLabel("图标").fill("🧳");
  await page.getByLabel("标签").fill("差旅, 审批");
  await page.getByRole("button", { name: "下一步" }).click();

  await page.getByLabel("模型提供方").fill("openai");
  await page.getByLabel("模型 ID").fill("gpt-4o-mini");
  await page.getByLabel("模型名称").fill("GPT-4o mini");
  await page.getByLabel("系统 Prompt").fill("你是差旅助手。");
  await page.getByRole("button", { name: "下一步" }).click();

  await page.getByText("天气查询", { exact: true }).click();
  await page.getByText("汇率换算", { exact: true }).click();
  await page.getByRole("button", { name: "下一步" }).click();

  // Reaching the preview must not have created anything; the request below is
  // the only one this journey is allowed to produce.
  const createButton = page.getByRole("button", { name: "创建智能体" });
  await expect(createButton).toBeVisible();
  await expect(createButton).toBeEnabled();
  await expect.poll(() => posts.length).toBe(0);

  await createButton.click();
  await expect.poll(() => posts.length).toBe(1);

  // The submitted body is the configuration the preview showed.
  expect(posts).toEqual([
    {
      agentId: "travel-bot",
      name: "差旅助手",
      description: "处理差旅申请",
      type: "agent",
      category: "行政",
      icon: "🧳",
      tags: ["差旅", "审批"],
      modelProvider: "openai",
      modelId: "gpt-4o-mini",
      modelName: "GPT-4o mini",
      prompt: "你是差旅助手。",
      knowledgeBaseIds: [],
      toolIds: ["tool-1", "tool-2"],
    },
  ]);
  await expect(page.getByRole("status").filter({ hasText: "创建成功" })).toBeVisible();
  await page.waitForURL("**/admin/agents/travel-bot");
  await expect(page.getByRole("heading", { name: "差旅助手" })).toBeVisible();
  await expect(page.getByText("草稿", { exact: true }).first()).toBeVisible();
});

test(`${TC_ID} submit: 创建失败时停留原页并给出失败提示`, async ({ page }) => {
  await page.route("**/api/tools", (route) => route.fulfill({ json: envelope(TOOLS) }));
  await page.route("**/api/agents", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 502,
      json: { error: { code: "DIFY_ERROR", message: "推理引擎暂不可用" } },
    });
  });

  await signInAsAgentAdmin(page);
  await page.goto("/admin/agents/new");
  await page.getByLabel("标识（agentId）").fill("travel-bot");
  await page.getByLabel("名称").fill("差旅助手");
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByRole("button", { name: "创建智能体" }).click();

  await expect(page.getByRole("status").filter({ hasText: "创建失败" })).toBeVisible();
  await expect(page).toHaveURL(/\/admin\/agents\/new$/);
  await expectHighlightedStep(page, 3);
});
