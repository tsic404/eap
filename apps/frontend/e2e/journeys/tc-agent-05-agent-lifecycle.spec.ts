import { expect, test, type Browser, type Locator, type Page } from "@playwright/test";

import { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";

// TC-agent-05 (AC-12) browser evidence: a freshly created agent renders the
// draft badge, 「开始测试」 is the one real path into testing (C-33 / ADR-018),
// publish and offline move the badge through green and red, the employee plaza
// only ever shows the published state, an existing conversation survives the
// offline transition, and the terminal state offers no publish entry.
//
// Every transition here is a real backend call on the QA stack — the badge is
// never faked with a replayed status. A second browser context carries the
// employee (dave) so visibility is judged by what that role actually receives.
//
// The design's clause "员工侧详情不再可见" has no browser surface: the app has
// no employee-facing agent detail route (`/user/agents/<id>` is a 404 in every
// state, and `/admin/agents/<id>` answers the permission notice), so the plaza
// assertion is the observable half; the API-side fixed not-found stays with the
// agent-service contract suite.

const TC_ID = "TC-agent-05";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:8090";

const ADMIN = LOCAL_ACCOUNTS.admin;
const EMPLOYEE = LOCAL_ACCOUNTS.employee;

const QA_MODEL_PROVIDER = "openai_api_compatible";
const QA_MODEL_ID = "mock-gpt";

/** The model stub answers with a fixed sentence, so a reply is exact. */
const STUB_REPLY = /确定性回答（[0-9a-f]{8}）：这是 QA 模型桩生成的固定文本。/;

type ColourFamily = "gray" | "blue" | "green" | "red";

interface Account {
  email: string;
  name: string;
  role: string;
}

/** A unique agent id/name per run: the QA stack is long-lived and shared. */
function uniqueKey(): string {
  return `${Date.now().toString(36)}-${Math.floor(Math.random() * 1e4)}`;
}

async function signIn(page: Page, account: Account): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(account.email);
  await page.getByLabel("口令").fill(LOCAL_ACCOUNT_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/user");
  // The auth bootstrap writes the backend role into the cookie the admin
  // routes read; wait for the profile before navigating there.
  await expect(page.getByText(account.email)).toBeVisible();
}

async function openAs(browser: Browser, account: Account): Promise<Page> {
  const context = await browser.newContext({ baseURL: BASE_URL });
  const page = await context.newPage();
  await signIn(page, account);
  return page;
}

/** Hue and saturation of the rendered text colour; the colour family is what the user sees. */
async function textColour(locator: Locator): Promise<{ hue: number; saturation: number }> {
  const rgb = await locator.evaluate((node) => getComputedStyle(node).color);
  const [red, green, blue] = rgb
    .match(/\d+/g)!
    .slice(0, 3)
    .map((value) => Number(value) / 255);
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  const delta = max - min;
  const saturation = max === 0 ? 0 : delta / max;
  if (delta === 0) return { hue: 0, saturation };
  let hue: number;
  if (max === red) {
    hue = 60 * (((green - blue) / delta) % 6);
  } else if (max === green) {
    hue = 60 * ((blue - red) / delta + 2);
  } else {
    hue = 60 * ((red - green) / delta + 4);
  }
  return { hue: (hue + 360) % 360, saturation };
}

function isFamily(hue: number, saturation: number, family: ColourFamily): boolean {
  switch (family) {
    case "gray":
      return saturation < 0.4;
    case "blue":
      return saturation >= 0.4 && hue >= 180 && hue <= 250;
    case "green":
      return saturation >= 0.4 && hue >= 90 && hue <= 165;
    case "red":
      return saturation >= 0.4 && (hue <= 15 || hue >= 345);
  }
}

/** The header badge for a status, judged by its text and rendered colour family. */
async function expectBadge(page: Page, label: string, family: ColourFamily): Promise<void> {
  // Scoped to the heading row: the same label also appears in the config tab
  // and in action toasts, and only this element is the lifecycle badge.
  const badge = page
    .locator("h1")
    .locator("xpath=..")
    .getByText(label, { exact: true });
  await expect(badge).toBeVisible();
  const { hue, saturation } = await textColour(badge);
  expect(
    isFamily(hue, saturation, family),
    `${label} renders as ${family} (hue ${hue.toFixed(1)}°, saturation ${saturation.toFixed(2)})`,
  ).toBe(true);
}

/**
 * Create a draft agent through the creation wizard — the user's own path, with
 * a real Dify app behind it, so the lifecycle that follows is genuinely real.
 */
async function createAgent(page: Page, agentId: string, name: string): Promise<void> {
  await page.goto("/admin/agents/new");
  await page.getByLabel("标识（agentId）").fill(agentId);
  await page.getByLabel("名称").fill(name);
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByLabel("模型提供方").fill(QA_MODEL_PROVIDER);
  await page.getByLabel("模型 ID").fill(QA_MODEL_ID);
  await page.getByLabel("模型名称").fill("Mock GPT");
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByRole("button", { name: "下一步" }).click();

  const [created] = await Promise.all([
    waitForAction(page, "/api/agents"),
    page.getByRole("button", { name: "创建智能体" }).click(),
  ]);
  expect(created.status()).toBe(201);
  expect((await created.json()).data.status).toBe("draft");
  await page.waitForURL(`**/admin/agents/${agentId}`);
}

/** The admin's live bearer token, read off the page's own authenticated calls. */
function captureAccessToken(page: Page): () => string {
  let token = "";
  page.on("request", (request) => {
    const header = request.headers()["authorization"];
    if (header?.startsWith("Bearer ")) token = header.slice("Bearer ".length);
  });
  return () => token;
}

/**
 * Wait for one action's settled response. The app's axios client replays a call
 * once after a 401 refresh, so the first leg can be an authentication retry —
 * the outcome is the response that carries the action's own status.
 */
function waitForAction(page: Page, path: string, method = "POST") {
  return page.waitForResponse(
    (response) =>
      response.url().endsWith(path) &&
      response.request().method() === method &&
      response.status() !== 401,
  );
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

/**
 * The employee plaza as that role receives it, after the page has settled.
 *
 * The component renders a skeleton while loading and a "加载失败" empty state on
 * error; only the success path renders the "智能体广场" heading. Waiting for the
 * heading therefore proves an absent card is the backend's answer, not a
 * skeleton that simply has no list yet — an error state never shows the heading.
 */
async function plazaCards(page: Page, name: string): Promise<Locator> {
  await page.goto("/user/agents");
  await expect(page.getByRole("heading", { name: "智能体广场" })).toBeVisible();
  await expect(page.getByText("加载失败")).toHaveCount(0);
  return page
    .getByRole("list", { name: "智能体广场列表" })
    .getByRole("listitem")
    .filter({ hasText: name });
}

async function startConversation(page: Page, name: string): Promise<void> {
  await page.goto("/user/conversations");
  await page.getByRole("button", { name: "新建会话" }).click();
  const picker = page.getByRole("dialog");
  await expect(picker).toBeVisible();
  await picker.getByRole("button", { name }).click();
  await page.waitForURL(/\/user\/conversations\/[0-9a-f-]+$/);
}

async function sendMessage(page: Page, text: string, expectedReplies: number): Promise<void> {
  const composer = page.getByPlaceholder("输入消息，Enter 发送，Shift+Enter 换行");
  await composer.fill(text);
  await composer.press("Enter");
  // The composer keeps the text until the turn commits; once it clears, the
  // transcript holds the bubble and the answer has completed.
  await expect(composer).toHaveValue("", { timeout: 60_000 });
  await expect(page.getByText(text, { exact: true })).toBeVisible();
  await expect(page.getByText(STUB_REPLY)).toHaveCount(expectedReplies, { timeout: 60_000 });
}

test(`${TC_ID} lifecycle: 草稿灰→开始测试蓝→发布绿→下线红，终态无发布入口且拒绝再次发布`, async ({
  page,
}) => {
  const token = captureAccessToken(page);
  const key = uniqueKey();
  const agentId = `e2e-lifecycle-${key}`;
  const name = `生命周期用例-${key}`;

  await signIn(page, ADMIN);
  await createAgent(page, agentId, name);
  await expectBadge(page, "草稿", "gray");

  // C-33 / ADR-018: 「开始测试」 is the one real transition into testing.
  const [started] = await Promise.all([
    waitForAction(page, `/api/agents/${agentId}/start-testing`),
    page.getByRole("button", { name: "开始测试" }).click(),
  ]);
  expect(started.status()).toBe(200);
  expect((await started.json()).data.status).toBe("testing");
  await expectBadge(page, "测试中", "blue");

  // Publish has its own confirmation and lands on green.
  await page.getByRole("button", { name: "发布" }).click();
  const publishDialog = page.getByRole("dialog");
  await expect(publishDialog).toBeVisible();
  const [published] = await Promise.all([
    waitForAction(page, `/api/agents/${agentId}/publish`),
    publishDialog.getByRole("button", { name: "发布" }).click(),
  ]);
  expect(published.status()).toBe(200);
  expect((await published.json()).data.status).toBe("published");
  await expectBadge(page, "已发布", "green");

  // Offline is terminal: red badge, and the publish entry is gone.
  await page.getByRole("button", { name: "下线" }).click();
  const offlineDialog = page.getByRole("dialog");
  await expect(offlineDialog).toBeVisible();
  const [offlined] = await Promise.all([
    waitForAction(page, `/api/agents/${agentId}/offline`),
    offlineDialog.getByRole("button", { name: "下线" }).click(),
  ]);
  expect(offlined.status()).toBe(200);
  expect((await offlined.json()).data.status).toBe("offline");
  await expectBadge(page, "已下线", "red");
  // The action area offers exactly one action on a terminal agent; any publish
  // entry — under any wording — changes this set.
  const actions = page.getByRole("button", { name: "删除" }).locator("xpath=..");
  await expect(actions.getByRole("button")).toHaveText(["删除"]);

  // "无法重新发布" is enforced by the backend on the same live session: a
  // publish attempt answers 409 INVALID_STATE naming the current state.
  expect(token()).not.toBe("");
  const detail = await (
    await page.request.get(`/api/agents/${agentId}`, { headers: authHeaders(token()) })
  ).json();
  const retry = await page.request.post(`/api/agents/${agentId}/publish`, {
    headers: authHeaders(token()),
    data: { version: detail.data.version },
  });
  expect(retry.status()).toBe(409);
  const failure = await retry.json();
  expect(failure.error.code).toBe("INVALID_STATE");
  expect(failure.error.message).toContain("offline");
});

test(`${TC_ID} visibility: 草稿与测试中不在员工广场，发布后可见，下线后隐藏且既有会话可继续`, async ({
  browser,
  page,
}) => {
  const key = uniqueKey();
  const agentId = `e2e-visibility-${key}`;
  const name = `可见性用例-${key}`;

  await signIn(page, ADMIN);
  await createAgent(page, agentId, name);

  const employee = await openAs(browser, EMPLOYEE);
  try {
    // Draft is invisible to the employee.
    await expect(await plazaCards(employee, name)).toHaveCount(0);

    // Testing is a lifecycle state, not a publication: still invisible.
    await page.getByRole("button", { name: "开始测试" }).click();
    await expectBadge(page, "测试中", "blue");
    await expect(await plazaCards(employee, name)).toHaveCount(0);

    // Published: the employee's own list now carries it.
    await page.getByRole("button", { name: "发布" }).click();
    const publishDialog = page.getByRole("dialog");
    await publishDialog.getByRole("button", { name: "发布" }).click();
    await expectBadge(page, "已发布", "green");
    await expect(await plazaCards(employee, name)).toHaveCount(1);

    // An employee starts a conversation while it is published.
    await startConversation(employee, name);
    const conversationUrl = employee.url();
    await sendMessage(employee, "发布后开始会话", 1);

    // Offline: the plaza hides it again, and the conversation keeps working.
    await page.getByRole("button", { name: "下线" }).click();
    const offlineDialog = page.getByRole("dialog");
    await offlineDialog.getByRole("button", { name: "下线" }).click();
    await expectBadge(page, "已下线", "red");

    await expect(await plazaCards(employee, name)).toHaveCount(0);

    await employee.goto(conversationUrl);
    await expect(employee.getByText("发布后开始会话", { exact: true })).toBeVisible();
    await sendMessage(employee, "下线后继续对话", 2);
  } finally {
    await employee.context().close();
  }
});
