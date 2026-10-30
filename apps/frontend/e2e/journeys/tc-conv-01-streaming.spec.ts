import { expect, test, type Page } from "@playwright/test";

import { envelope, mockConversation, PROXY_ORIGIN } from "../helpers/api";
import {
  loadFixtures,
  openApiSession,
  signIn,
  STACK_ORIGIN,
  type FixtureManifest,
} from "../helpers/fixtures";

// TC-conv-01 (AC-22, 03-acceptance.md @ anchor dfa07d3:69): the streamed turn
// shows the user bubble immediately (blue, right-aligned), refuses a second
// send while streaming, types the answer out with a blinking caret, finishes as
// rendered Markdown, shows this turn's copyable trace id — the clipboard must
// return exactly the traceId the turn's `message_end` frame carried — and
// offers no jump into run logs or trace details.
//
// Prerequisite for the primary journey: the full local-family stack with a Dify
// workspace provider.
//   make e2e E2E_SPEC=e2e/journeys/tc-conv-01-streaming.spec.ts
// The QA variant ships the engine and its deterministic provider (`mock-gpt`),
// so this command runs the real chain end to end. Without that provider (the
// host harness used by CI) the real journey skips and the auxiliary replay below
// still covers the rendered-Markdown clause, which the deterministic provider's
// fixed plain-text answer cannot produce.
export const TC_ID = "TC-conv-01";

const COMPOSER = "输入消息，Enter 发送，Shift+Enter 换行";
const AGENT_NAME = "流式探针智能体";
const MODEL_NAME = "mock-gpt";
const PROVIDER_HINT = "openai_api_compatible";
const CONV_PROBE_PREFIX = "e2e-conv01-";

const AUX_CONVERSATION_ID = "conv-conv-01-aux";
const AUX_CHUNKS = [
  "这是一段用于验证逐字渲染的较长回答内容，每个字都应当被逐个显示出来。",
  "**加粗** 与 `代码` 也应当渲染。",
  "\n\n- 第一项\n- 第二项",
];

interface CrossTenantTenant {
  email: string;
}

const { crossTenant } = loadFixtures() as FixtureManifest & {
  crossTenant: { tenantA: CrossTenantTenant };
};
const TENANT_A_EMAIL = crossTenant.tenantA.email;

function pathOf(source: { url(): string }): string {
  return new URL(source.url()).pathname;
}

function difyReady(body: unknown): boolean {
  if (typeof body !== "object" || body === null || !("checks" in body)) return false;
  const { checks } = body as { checks: unknown };
  if (typeof checks !== "object" || checks === null || !("dify" in checks)) return false;
  const { dify } = checks as { dify: unknown };
  return dify === "ok";
}

/** The primary token resolved to the same computed colour a bubble reports. */
async function primaryBackground(page: Page): Promise<string> {
  return page.evaluate(() => {
    const probe = document.createElement("div");
    probe.style.backgroundColor = "var(--color-primary)";
    document.body.append(probe);
    const value = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return value;
  });
}

interface StreamProbe {
  caretSeen: boolean;
  samples: number[];
}

/** Record the caret and the streamed text length on every DOM mutation. */
async function installStreamProbe(page: Page): Promise<void> {
  await page.evaluate(() => {
    const state: StreamProbe = { caretSeen: false, samples: [] };
    (window as Window & { __streamProbe?: StreamProbe }).__streamProbe = state;
    const record = () => {
      const caret = document.querySelector(".streaming-caret");
      if (caret === null) return;
      state.caretSeen = true;
      state.samples.push((caret.parentElement?.innerText ?? "").trim().length);
    };
    const observer = new MutationObserver(record);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  });
}

async function readStreamProbe(page: Page): Promise<StreamProbe> {
  const probe = await page.evaluate(
    () => (window as Window & { __streamProbe?: StreamProbe }).__streamProbe,
  );
  if (probe === undefined) throw new Error("the stream probe was not installed");
  return probe;
}

/** Select the identifier's characters and copy them with the keyboard. */
async function copyTraceId(page: Page, traceId: string): Promise<string> {
  await page.evaluate((id) => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode as Text;
      const start = node.data.indexOf(id);
      if (start === -1) continue;
      const range = document.createRange();
      range.setStart(node, start);
      range.setEnd(node, start + id.length);
      const selection = window.getSelection();
      if (selection === null) throw new Error("the document has no selection object");
      selection.removeAllRanges();
      selection.addRange(range);
      return;
    }
    throw new Error(`trace id ${id} is not rendered as selectable text`);
  }, traceId);
  await page.keyboard.press("Control+c");
  return page.evaluate(() => navigator.clipboard.readText());
}

test("real stream: bubble, guard, typewriter, trace copy and no trace jump", async ({
  page,
  request,
  playwright,
  context,
}) => {
  const ready = await request.get(`${STACK_ORIGIN}/api/health/ready`);
  const readyBody: unknown = ready.status() === 200 ? await ready.json() : null;
  test.skip(
    !(ready.status() === 200 && difyReady(readyBody)),
    "requires the full stack with a Dify workspace provider",
  );

  const api = await openApiSession(playwright.request, { email: TENANT_A_EMAIL });
  const agentId = `${CONV_PROBE_PREFIX}${Date.now().toString(36)}`;
  let conversationId: string | null = null;
  try {
    const models = await api.get("/api/models");
    expect(models.status(), await models.text()).toBe(200);
    const providers = ((await models.json()) as { data: { provider: string }[] }).data;
    const provider = providers.find((entry) => entry.provider.includes(PROVIDER_HINT));
    expect(provider, "the QA stack's deterministic provider is installed").toBeTruthy();

    const created = await api.post("/api/agents", {
      data: {
        agentId,
        name: AGENT_NAME,
        description: "流式链路探针",
        type: "chat",
        modelProvider: provider?.provider,
        modelId: MODEL_NAME,
        modelName: MODEL_NAME,
        prompt: "请直接回答用户。",
      },
    });
    expect(created.status(), await created.text()).toBe(201);
    const agent = (await created.json()) as { data: { version: number } };

    const published = await api.post(`/api/agents/${agentId}/publish`, {
      data: { version: agent.data.version },
    });
    expect(published.status(), await published.text()).toBe(200);

    const conversationResponse = await api.post("/api/conversations", {
      data: { agentId },
    });
    expect(conversationResponse.status(), await conversationResponse.text()).toBe(201);
    conversationId = ((await conversationResponse.json()) as { data: { id: string } }).data.id;

    await signIn(page, { email: TENANT_A_EMAIL });
    await page.goto(`/user/conversations/${conversationId}`);
    await expect(page.getByText(AGENT_NAME)).toBeVisible();

    let messagePosts = 0;
    page.on("request", (candidate) => {
      if (
        candidate.method() === "POST" &&
        pathOf(candidate) === `/api/conversations/${conversationId}/messages`
      ) {
        messagePosts += 1;
      }
    });
    await installStreamProbe(page);

    const query = `请回答第 ${Date.now()} 轮`;
    await page.getByPlaceholder(COMPOSER).fill(query);

    const streamed = page.waitForResponse(
      (candidate) =>
        candidate.request().method() === "POST" &&
        pathOf(candidate) === `/api/conversations/${conversationId}/messages`,
    );
    await page.getByRole("button", { name: "发送" }).click();

    // The user bubble is in the transcript before the first frame arrives, is
    // right-aligned and carries the primary colour.
    const bubble = page.getByText(query, { exact: true }).first().locator("xpath=..");
    await expect(bubble).toBeVisible();
    await expect(bubble.locator("xpath=..")).toHaveCSS("justify-content", "flex-end");
    expect(await bubble.evaluate((node) => getComputedStyle(node).backgroundColor)).toBe(
      await primaryBackground(page),
    );

    // While the turn streams the composer refuses a second send: the send
    // button is a stop button, Enter adds no bubble, and only one POST runs.
    await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible();
    await page.getByPlaceholder(COMPOSER).fill("这条在流式期间不应发出");
    await page.getByPlaceholder(COMPOSER).press("Enter");
    await expect(page.getByText("这条在流式期间不应发出")).toHaveCount(0);
    expect(messagePosts).toBe(1);

    const response = await streamed;
    expect(response.status()).toBe(200);
    const sse = await response.text();
    const endFrame = sse
      .split("\n\n")
      .find((frame) => frame.includes("event: message_end"));
    if (endFrame === undefined) throw new Error("the turn carried no message_end frame");
    const traceId = /"traceId"\s*:\s*"([^"]+)"/.exec(endFrame)?.[1];
    if (traceId === undefined) throw new Error("the end frame carried no traceId");

    // The finished answer is rendered and the typewriter really revealed it
    // character by character: the probe saw the caret and at least two distinct
    // visible lengths while it was active.
    const answer = page.getByText(/确定性回答（/).first();
    await expect(answer).toBeVisible();
    await expect(page.locator(".streaming-caret")).toHaveCount(0);
    const probe = await readStreamProbe(page);
    expect(probe.caretSeen).toBe(true);
    expect(new Set(probe.samples).size).toBeGreaterThan(1);

    // The header carries this turn's trace id; copying the identifier text gives
    // back exactly the traceId the end frame carried.
    const traceText = page.getByText(`Trace: ${traceId}`, { exact: true });
    await expect(traceText).toBeVisible();
    await context.grantPermissions(["clipboard-read", "clipboard-write"], {
      origin: PROXY_ORIGIN,
    });
    await page.bringToFront();
    expect(await copyTraceId(page, traceId)).toBe(traceId);

    // Nothing on the page navigates into run logs or trace details.
    await expect(page.locator('a[href*="/admin/run-logs"]')).toHaveCount(0);
    await expect(page.locator(`a[href*="${traceId}"]`)).toHaveCount(0);
    await expect(page.getByRole("link", { name: new RegExp(traceId) })).toHaveCount(0);
    const urlBefore = page.url();
    await traceText.click();
    await expect(page).toHaveURL(urlBefore);
  } finally {
    if (conversationId !== null) {
      await api.delete(`/api/conversations/${conversationId}`).catch(() => undefined);
    }
    await api.delete(`/api/agents/${agentId}`).catch(() => undefined);
    await api.dispose();
  }
});

/** Replace the message POST with a timed SSE stream built from fixed chunks. */
async function replayMarkdownStream(page: Page): Promise<void> {
  await page.addInitScript(
    ({ conversationId, chunks }: { conversationId: string; chunks: string[] }) => {
      const originalFetch = window.fetch.bind(window);
      window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : input.toString();
        const method = init?.method ?? (input instanceof Request ? input.method : "GET");
        if (method !== "POST" || !url.includes(`/conversations/${conversationId}/messages`)) {
          return originalFetch(input, init);
        }
        const frames = chunks.map(
          (content) =>
            `event: message\ndata: ${JSON.stringify({ content, messageId: "assistant-aux" })}\n\n`,
        );
        frames.push(
          `event: message_end\ndata: ${JSON.stringify({ traceId: "trace-aux", metadata: {} })}\n\n`,
        );
        const encoder = new TextEncoder();
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            let index = 0;
            const emit = () => {
              controller.enqueue(encoder.encode(frames[index]));
              index += 1;
              if (index < frames.length) {
                window.setTimeout(emit, 1800);
              } else {
                controller.close();
              }
            };
            window.setTimeout(emit, 300);
          },
        });
        return Promise.resolve(
          new Response(body, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          }),
        );
      }) as typeof window.fetch;
    },
    { conversationId: AUX_CONVERSATION_ID, chunks: AUX_CHUNKS },
  );
}

test("auxiliary replay: a Markdown answer is rendered completely", async ({ page }) => {
  await signIn(page, { email: TENANT_A_EMAIL });
  await replayMarkdownStream(page);
  await page.route(`**/api/conversations/${AUX_CONVERSATION_ID}`, (route) =>
    route.fulfill({
      json: envelope(
        mockConversation({
          id: AUX_CONVERSATION_ID,
          title: "Markdown 辅助用例",
          agentName: AGENT_NAME,
        }),
      ),
    }),
  );
  await page.route(`**/api/conversations/${AUX_CONVERSATION_ID}/messages*`, (route) =>
    route.fulfill({ json: envelope({ items: [], nextCursor: null }) }),
  );

  await page.goto(`/user/conversations/${AUX_CONVERSATION_ID}`);
  await expect(page.getByText(AGENT_NAME)).toBeVisible();

  await page.getByPlaceholder(COMPOSER).fill("请渲染 Markdown");
  await page.getByRole("button", { name: "发送" }).click();

  // The caret is active while the replay streams, then the whole Markdown body
  // is present and the caret is gone.
  await expect(page.locator(".streaming-caret")).toHaveCount(1, { timeout: 10_000 });
  await expect(page.getByText("第二项")).toBeVisible();
  await expect(page.locator(".streaming-caret")).toHaveCount(0);
  await expect(page.locator("strong", { hasText: "加粗" })).toBeVisible();
  await expect(page.locator("code", { hasText: "代码" })).toBeVisible();
});
