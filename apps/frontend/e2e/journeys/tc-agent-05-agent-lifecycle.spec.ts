import { expect, test, type Locator, type Page } from "@playwright/test";

import type { AgentDetail } from "@/lib/agent-types";

import { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";
import { envelope } from "../helpers/api";

// TC-agent-05 (AC-12) browser evidence: the status badge walks the lifecycle
// 草稿(灰) → 测试中(蓝) → 已发布(绿) → 已下线(红), and a down-lined agent offers
// no actionable publish entry — offline is terminal (ADR-015).
//
// What is asserted here is the admin surface an operator can see. The cross-role
// half of the criterion (published agent visible in the employee plaza, hidden
// again after offline, existing conversations still usable) needs a real agent
// on the stack, because visibility is enforced by the backend list (C-34) and
// cannot be replayed at the browser edge honestly.

const TC_ID = "TC-agent-05";

type ColourFamily = "gray" | "blue" | "green" | "red";

const AGENT: AgentDetail = {
  agentId: "bot-1",
  tenantId: "tenant-1",
  difyAppId: "dify-app-1",
  name: "客服助手",
  description: "回答售前咨询",
  type: "chat",
  category: "客服",
  icon: "🤖",
  tags: [],
  status: "draft",
  modelId: null,
  modelName: null,
  modelProvider: null,
  prompt: null,
  version: 1,
  createdBy: "user-1",
  publishedAt: null,
  publishedBy: null,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
  boundKnowledge: [],
  boundTools: [],
  conversationCount: 0,
  recentLogs: [],
  conversationCount: 0,
};

const STATES: { status: string; label: string; family: ColourFamily }[] = [
  { status: "draft", label: "草稿", family: "gray" },
  { status: "testing", label: "测试中", family: "blue" },
  { status: "published", label: "已发布", family: "green" },
  { status: "offline", label: "已下线", family: "red" },
];

/** Hue and saturation of the rendered text colour; the色族 is what the user sees. */
async function textColour(locator: Locator): Promise<{
  hue: number;
  saturation: number;
}> {
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

async function signInAsAgentAdmin(page: Page): Promise<void> {
  const admin = LOCAL_ACCOUNTS.admin;
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(admin.email);
  await page.getByLabel("口令").fill(LOCAL_ACCOUNT_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/user");
  await expect(page.getByText(admin.email)).toBeVisible();
}

test(`${TC_ID} badge: 状态徽章文案与色彩按草稿→测试中→已发布→已下线流转`, async ({
  page,
}) => {
  let status = "draft";
  await page.route("**/api/agents/bot-1", (route) =>
    route.fulfill({ json: envelope({ ...AGENT, status }) }),
  );
  await signInAsAgentAdmin(page);

  for (const state of STATES) {
    status = state.status;
    await page.goto("/admin/agents/bot-1");
    // 整页重载会各发一次 refresh；等它换完 cookie 再进下一状态，否则轮换被判为复用。
    await page.waitForLoadState("networkidle");
    const badge = page.getByText(state.label, { exact: true }).first();
    await expect(badge).toBeVisible();
    const { hue, saturation } = await textColour(badge);
    expect(
      isFamily(hue, saturation, state.family),
      `${state.label} renders as ${state.family} (hue ${hue.toFixed(1)}°, saturation ${saturation.toFixed(2)})`,
    ).toBe(true);
  }
});

test(`${TC_ID} terminal: 已下线状态下不存在可操作的发布入口`, async ({ page }) => {
  await page.route("**/api/agents/bot-1", (route) =>
    route.fulfill({ json: envelope({ ...AGENT, status: "offline" }) }),
  );
  await signInAsAgentAdmin(page);
  await page.goto("/admin/agents/bot-1");

  await expect(page.getByText("已下线", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "发布" })).toHaveCount(0);
  // The rest of the lifecycle surface stays usable: deletion is still offered.
  await expect(page.getByRole("button", { name: "删除" })).toBeVisible();
});
