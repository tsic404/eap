import { expect, test, type Page } from "@playwright/test";

import type { AgentDetail } from "@/lib/agent-types";

import { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS } from "../helpers/accounts.cjs";
import { envelope, PROXY_ORIGIN } from "../helpers/api";

// TC-agent-04 (AC-11) browser evidence: the detail page exposes the 配置/调试/日志
// tabs, and the debug tab runs a real conversation — the user bubble appears at
// once, the answer arrives character by character from the backend's stream, and
// the finished turn exposes a copyable trace id.
//
// Prerequisite, not mocked here: a stack whose readiness probe reports the
// inference engine as ok. The QA variant supplies it (deterministic model stub
// behind a real engine); the journey skips only when that provider is absent,
// because replaying the stream at the browser edge would prove nothing.
//
//   make e2e E2E_SPEC=e2e/journeys/tc-agent-04-agent-debug.spec.ts
//
// The tabs journey replays the agent metadata so it does not depend on how the
// agent was created. The debug journey creates its own draft agent through the
// page's authenticated session, so a fresh stack needs no seeded fixture.

const TC_ID = "TC-agent-04";

const AGENT: AgentDetail = {
  agentId: "bot-1",
  tenantId: "tenant-1",
  difyAppId: "dify-app-1",
  name: "客服助手",
  description: "回答售前咨询",
  type: "chat",
  category: "客服",
  icon: "🤖",
  tags: ["客服"],
  status: "published",
  modelId: "gpt-4o-mini",
  modelName: "GPT-4o mini",
  modelProvider: "openai",
  prompt: "你是客服助手。",
  version: 2,
  createdBy: "user-1",
  publishedAt: "2026-01-02T00:00:00Z",
  publishedBy: "user-1",
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-02T00:00:00Z",
  boundKnowledge: [],
  boundTools: [],
  conversationCount: 0,
  recentLogs: [],
};

async function signInAsAgentAdmin(page: Page): Promise<void> {
  const admin = LOCAL_ACCOUNTS.admin;
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(admin.email);
  await page.getByLabel("口令").fill(LOCAL_ACCOUNT_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL("**/user");
  await expect(page.getByText(admin.email)).toBeVisible();
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

/** A unique id per run: the QA stack is long-lived and shared. */
function uniqueKey(): string {
  return `${Date.now().toString(36)}-${Math.floor(Math.random() * 1e4)}`;
}

/**
 * Create the agent whose debug turn the journey drives. The turn needs a real
 * Dify app and model, so the journey makes its own fixture instead of assuming
 * a seeded row exists on a fresh stack.
 */
async function createAgent(
  page: Page,
  token: string,
  agentId: string,
  name: string,
): Promise<void> {
  const created = await page.request.post("/api/agents", {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      agentId,
      name,
      type: "chat",
      modelProvider: "openai_api_compatible",
      modelId: "mock-gpt",
      modelName: "Mock GPT",
    },
  });
  expect(created.status()).toBe(201);
}

// The debug turn is a real inference call. Without a Dify workspace provider
// the backend refuses the prepare phase — a precondition, not a UI defect, so
// the journey skips. Nothing else may skip: the agent's existence and its
// upstream key are proved by the page's own authenticated requests.
async function debugPrecondition(page: Page): Promise<string | null> {
  const ready: unknown = await (await page.request.get("/api/health/ready")).json();
  const checks =
    ready && typeof ready === "object" && "checks" in ready ? ready.checks : undefined;
  const dify =
    checks && typeof checks === "object" && "dify" in checks ? checks.dify : undefined;
  if (dify !== "ok") {
    return "needs a Dify workspace provider (DIFY_*): /api/health/ready reports dify error";
  }
  return null;
}

test(`${TC_ID} tabs: 配置/调试/日志三页签可切换`, async ({ page }) => {
  await page.route("**/api/agents/bot-1", (route) =>
    route.fulfill({ json: envelope(AGENT) }),
  );
  await signInAsAgentAdmin(page);
  await page.goto("/admin/agents/bot-1");

  const tabs = page.getByRole("tab");
  await expect(tabs).toHaveCount(3);
  await expect(page.getByRole("tab", { name: "配置" })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  await page.getByRole("tab", { name: "调试" }).click();
  await expect(page.getByRole("tab", { name: "调试" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("tab", { name: "配置" })).toHaveAttribute(
    "aria-selected",
    "false",
  );

  await page.getByRole("tab", { name: "日志" }).click();
  await expect(page.getByRole("tab", { name: "日志" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("tabpanel")).toBeVisible();
});

test(`${TC_ID} debug: 发送测试消息后气泡即时、回答逐字流式并给出可复制轨迹标识`, async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: PROXY_ORIGIN,
  });
  const token = captureAccessToken(page);
  await signInAsAgentAdmin(page);
  const missing = await debugPrecondition(page);
  if (missing !== null) test.skip(true, missing);
  expect(token()).not.toBe("");
  const agentId = `e2e-debug-${uniqueKey()}`;
  await createAgent(page, token(), agentId, `调试用例-${agentId}`);
  await page.goto(`/admin/agents/${agentId}`);
  await page.getByRole("tab", { name: "调试" }).click();

  const panel = page.getByRole("tabpanel");
  const input = panel.getByRole("textbox");
  await expect(input).toBeVisible();

  const QUERY = "请用一句话介绍你自己";
  // Record, in the page's own clock, when the user bubble mounts and when the
  // answer first becomes non-empty, alongside every answer mutation for the
  // character-by-character judgement. "立即出现" is the ordering of those two
  // moments; a bubble found visible ten seconds later proves nothing.
  await panel.evaluate((node, query) => {
    const target = node as HTMLElement & {
      __samples?: string[];
      __bubbleAt?: number | null;
      __answerAt?: number | null;
    };
    target.__samples = [];
    target.__bubbleAt = null;
    target.__answerAt = null;
    const observer = new MutationObserver(() => {
      const now = performance.now();
      if (target.__bubbleAt === null) {
        const bubble = Array.from(target.querySelectorAll("p")).find(
          (element) => element.textContent?.trim() === query,
        );
        if (bubble) target.__bubbleAt = now;
      }
      const answer = target.querySelector("[data-role='assistant-message']");
      const text = answer?.textContent?.trim() ?? "";
      if (text.length > 0) {
        if (target.__answerAt === null) target.__answerAt = now;
        if (text !== target.__samples?.at(-1)) target.__samples?.push(text);
      }
    });
    observer.observe(node, { childList: true, subtree: true, characterData: true });
  }, QUERY);

  await input.fill(QUERY);
  await input.press("Enter");

  const answer = panel.locator("[data-role='assistant-message']");
  await expect(answer).not.toHaveText("", { timeout: 60_000 });
  await expect(panel.getByText(QUERY, { exact: true })).toBeVisible();

  const timing = await panel.evaluate((node) => {
    const target = node as HTMLElement & {
      __bubbleAt?: number | null;
      __answerAt?: number | null;
    };
    return { bubbleAt: target.__bubbleAt ?? null, answerAt: target.__answerAt ?? null };
  });
  expect(timing.bubbleAt, "user bubble mounted").not.toBeNull();
  expect(timing.answerAt, "answer text started").not.toBeNull();
  if (timing.bubbleAt === null || timing.answerAt === null) {
    throw new Error("turn timing was not observed");
  }
  expect(
    timing.bubbleAt,
    "the user bubble mounts before the first answer text",
  ).toBeLessThanOrEqual(timing.answerAt);
  const samples = await panel.evaluate(
    (node) => ((node as HTMLElement & { __samples?: string[] }).__samples ?? []),
  );
  const finalText = (await answer.textContent())?.trim() ?? "";
  const prefixes = samples.filter((sample) => sample.length < finalText.length);
  expect(prefixes.length, "at least one intermediate rendering").toBeGreaterThan(0);
  for (const sample of samples) {
    expect(finalText.startsWith(sample), "stream samples grow as prefixes").toBe(true);
  }

  // The completed turn carries its trace id with a copy affordance.
  const trace = panel.getByText(/^Trace:/);
  await expect(trace).toBeVisible();
  const traceId = ((await trace.textContent()) ?? "").replace(/^Trace:\s*/, "").trim();
  expect(traceId.length).toBeGreaterThan(0);

  await panel.getByRole("button", { name: /复制/ }).click();
  const clipboard = await page.evaluate(() => navigator.clipboard.readText());
  expect(clipboard).toBe(traceId);
});
