import { expect, test, type Page } from "@playwright/test";

import { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";
import { envelope, mockAgent, PROXY_ORIGIN } from "../helpers/api";

// TC-sec-02 (AC-43), frontend judgement: an empty tenant never renders a blank
// table or empty-array placeholder. The admin lists show the create-first
// guidance card — with a working entry — and `/admin/agents` is a real route
// behind the admin gate (C-15), not a 404. On the employee side the marketplace
// and the workspace recommendation section state that the administrator has
// published no agent, with the illustration that carries that fact.

const PLATFORM_ADMIN = LOCAL_ACCOUNTS.platformAdmin;
const EMPLOYEE = LOCAL_ACCOUNTS.employee;

async function signIn(page: Page, account: { email: string } = PLATFORM_ADMIN) {
  await page.context().addCookies([
    { name: "NEXT_LOCALE", value: "zh-CN", url: PROXY_ORIGIN },
  ]);
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(account.email);
  await page.getByLabel("口令").fill(LOCAL_ACCOUNT_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/user");
  // Wait for the bootstrap refresh to finish before a full navigation, so the
  // rotated refresh cookie is the one the next page load carries.
  await expect(page.getByText(account.email)).toBeVisible();
}

/** Answer every list read as an empty page: the empty-tenant state under test. */
async function stubEmptyLists(page: Page) {
  await page.route("**/api/agents*", (route) =>
    route.fulfill({
      json: envelope({ items: [], total: 0, page: 1, pageSize: 100 }),
    }),
  );
  await page.route("**/api/knowledge-bases*", (route) =>
    route.fulfill({ json: envelope({ items: [], total: 0 }) }),
  );
  await page.route("**/api/tools*", (route) => route.fulfill({ json: envelope([]) }));
}

test("TC-sec-02 admin: 三处空态呈现创建引导卡片", async ({ page }) => {
  await signIn(page);
  await stubEmptyLists(page);

  const agentsResponse = await page.goto("/admin/agents");
  expect(agentsResponse?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "智能体", level: 1 })).toBeVisible();
  const agentsCard = page.getByRole("region", { name: "创建第一个智能体" });
  await expect(agentsCard).toBeVisible();
  await expect(agentsCard.getByRole("button", { name: "新建智能体" })).toBeVisible();
  await expect(page.getByRole("list", { name: "智能体列表" })).toHaveCount(0);

  // The card's entry is the real creation wizard, not a dead control.
  await agentsCard.getByRole("button", { name: "新建智能体" }).click();
  await expect(page).toHaveURL(/\/admin\/agents\/new$/);

  await page.goto("/admin/knowledge");
  const knowledgeCard = page.getByRole("region", { name: "创建第一个知识库" });
  await expect(knowledgeCard).toBeVisible();
  await expect(knowledgeCard.getByRole("button", { name: "新建知识库" })).toBeVisible();
  await expect(page.getByRole("list", { name: "知识库列表" })).toHaveCount(0);

  await page.goto("/admin/tools");
  const toolsCard = page.getByRole("region", { name: "创建第一个工具" });
  await expect(toolsCard).toBeVisible();
  await expect(toolsCard.getByRole("button", { name: "新建工具" })).toBeVisible();
  await expect(page.getByRole("list", { name: "工具列表" })).toHaveCount(0);
});

test("TC-sec-02 admin: 智能体卡片可用键盘进入详情", async ({ page }) => {
  await signIn(page);
  await page.route(/\/api\/agents(\?.*)?$/, (route) =>
    route.fulfill({
      json: envelope({ items: [mockAgent()], total: 1, page: 1, pageSize: 100 }),
    }),
  );

  await page.goto("/admin/agents");
  const link = page.getByRole("link", { name: /测试智能体/ });
  await expect(link).toBeVisible();

  // The card link is the page's only way into a detail view: Tab must reach it.
  let reached = false;
  for (let presses = 0; presses < 60 && !reached; presses += 1) {
    await page.keyboard.press("Tab");
    reached = await link.evaluate((element) => element === document.activeElement);
  }
  expect(reached, "focus reaches the agent card link by keyboard").toBe(true);

  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/admin\/agents\/ag-1$/);
});

test("TC-sec-02 admin: 无管理权限的访客在 /admin/agents 得到权限不足页", async ({
  page,
}) => {
  await page.goto("/admin/agents");

  // The address bar keeps the requested path; the body is the friendly notice.
  await expect(page).toHaveURL(/\/admin\/agents$/);
  await expect(page.getByRole("heading", { name: "无管理权限" })).toBeVisible();
});

test("TC-sec-02 employee: 无已发布智能体时呈现未发布引导插画与文案", async ({
  page,
}) => {
  await signIn(page, EMPLOYEE);
  await stubEmptyLists(page);
  await page.route("**/api/dashboard/user*", (route) =>
    route.fulfill({
      json: envelope({
        recommendedAgents: [],
        recentConversations: [],
        pendingTaskCount: 0,
      }),
    }),
  );

  const marketplaceResponse = await page.goto("/user/agents");
  expect(marketplaceResponse?.status()).toBe(200);
  await expect(
    page.getByRole("heading", { name: "智能体广场", level: 1 }),
  ).toBeVisible();
  const marketplaceGuidance = page.getByRole("region", {
    name: "您的管理员尚未发布智能体",
  });
  await expect(marketplaceGuidance).toBeVisible();
  await expect(
    marketplaceGuidance.getByRole("img", { name: "尚未发布智能体" }),
  ).toBeVisible();
  await expect(marketplaceGuidance.getByText(/联系管理员/)).toBeVisible();
  // Nothing is published, so no asset surface renders behind the guidance.
  await expect(page.getByRole("table")).toHaveCount(0);

  await page.goto("/user");
  await expect(page.getByRole("heading", { name: "用户工作区" })).toBeVisible();
  const homeGuidance = page.getByRole("region", {
    name: "您的管理员尚未发布智能体",
  });
  await expect(homeGuidance).toBeVisible();
  await expect(
    homeGuidance.getByRole("img", { name: "尚未发布智能体" }),
  ).toBeVisible();
  // The conversation section keeps its own empty state.
  await expect(page.getByText("暂无会话")).toBeVisible();
});
