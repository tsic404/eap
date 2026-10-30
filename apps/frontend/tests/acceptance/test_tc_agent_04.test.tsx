/**
 * TC-agent-04 — debug tab: 配置/调试/日志 switch, an immediate user bubble, a
 * character-by-character answer and a copyable trace id (AC-11).
 *
 * The browser drives the real panel and the real `useAgentDebug` stream reader;
 * only `fetch` is stubbed, so the assertions cover the request the panel sends
 * (C-127) and what it renders from the returned SSE frames. The turn itself is
 * a real model call in the browser suite
 * (`e2e/journeys/tc-agent-04-agent-debug.spec.ts`), which needs a Dify-backed
 * stack; this module judges the interface contract without one.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import zhCN from "@/messages/zh-CN.json";
import type { AgentDetail } from "@/lib/agent-types";

import { AgentConfigPanel } from "@/components/agents/agent-config-panel";

export const TC_ID = "TC-agent-04";

const AGENT_ID = "bot-1";
const QUERY = "请用一句话介绍你自己";
const TRACE_ID = "trace-debug-0001";
const ANSWER = "我是客服助手，随时为你解答。";

const AGENT: AgentDetail = {
  agentId: AGENT_ID,
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

/** One SSE frame pair as the backend emits it (`event:` + single-line JSON). */
function frame(event: string, data: Record<string, unknown>): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function sleep(ms: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, ms);
  return promise;
}

/** A streaming `Response` that delivers `frames` one per delay. */
function sseResponse(frames: readonly string[], delayMs = 40): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const raw of frames) {
        await sleep(delayMs);
        controller.enqueue(encoder.encode(raw));
      }
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

function renderPanel() {
  return render(
    <NextIntlClientProvider locale="zh-CN" messages={zhCN}>
      <AgentConfigPanel agent={AGENT} />
    </NextIntlClientProvider>,
  );
}

function openDebugTab() {
  fireEvent.click(screen.getByRole("tab", { name: "调试" }));
}

function typeQuery(query = QUERY) {
  const input = screen.getByRole("textbox", { name: "测试消息" });
  fireEvent.change(input, { target: { value: query } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
}

describe(`${TC_ID} 调试页签`, () => {
  const writeText = vi.fn();
  let fetchMock: Mock;

  beforeEach(() => {
    writeText.mockReset();
    writeText.mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    // jsdom's rAF exists but never fires without a visual viewport; a timer
    // drives the reveal at the same cadence so the typewriter is observable.
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
      window.setTimeout(() => callback(performance.now()), 16),
    );
    vi.stubGlobal("cancelAnimationFrame", (handle: number) => window.clearTimeout(handle));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("配置/调试/日志三页签可切换", () => {
    renderPanel();

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "配置",
      "调试",
      "日志",
    ]);
    expect(screen.getByRole("tab", { name: "配置" }).getAttribute("aria-selected")).toBe(
      "true",
    );

    openDebugTab();
    expect(screen.getByRole("tab", { name: "调试" }).getAttribute("aria-selected")).toBe(
      "true",
    );
    expect(screen.getByRole("tab", { name: "配置" }).getAttribute("aria-selected")).toBe(
      "false",
    );

    fireEvent.click(screen.getByRole("tab", { name: "日志" }));
    expect(screen.getByRole("tab", { name: "日志" }).getAttribute("aria-selected")).toBe(
      "true",
    );
    expect(screen.getByRole("tabpanel")).toBeTruthy();
  });

  it("用户气泡即时出现，回答逐字流式渲染，完成后给出可复制的轨迹标识", async () => {
    const samples: string[] = [];
    const observer = new MutationObserver(() => {
      const answer = document.querySelector("[data-role='assistant-message']");
      const text = answer?.textContent?.trim() ?? "";
      if (text && text !== samples.at(-1)) samples.push(text);
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    // Three deltas, so a panel that simply swaps each delta in would produce at
    // most three renderings; "character by character" means strictly more.
    const deltas = ["我是", "客服助手", "，随时为你解答。"];
    fetchMock.mockResolvedValue(
      sseResponse([
        ...deltas.map((content) => frame("message", { content, messageId: "msg-1" })),
        frame("message_end", { traceId: TRACE_ID }),
      ]),
    );

    renderPanel();
    openDebugTab();
    typeQuery();

    // The bubble is on screen before the first delta has been applied.
    expect(screen.getByText(QUERY)).toBeTruthy();
    expect(document.querySelector("[data-role='assistant-message']")).toBeNull();

    // The request is the debug turn of this agent, carrying the test message.
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/agents/${AGENT_ID}/debug`),
      expect.objectContaining({ method: "POST", body: JSON.stringify({ query: QUERY }) }),
    );

    const trace = await screen.findByText(/^Trace:/, undefined, { timeout: 5_000 });
    const answer = await waitFor(
      () => {
        const node = document.querySelector("[data-role='assistant-message']");
        if (!node?.textContent?.includes(ANSWER)) throw new Error("answer still revealing");
        return node;
      },
      { timeout: 5_000 },
    );
    observer.disconnect();

    expect(trace.textContent).toContain(`Trace: ${TRACE_ID}`);

    // "Character by character": the answer was rendered in growing prefixes,
    // in more steps than the stream delivered deltas.
    const finalText = answer?.textContent?.trim() ?? "";
    expect(samples.length).toBeGreaterThan(deltas.length);
    for (const sample of samples) {
      expect(finalText.startsWith(sample)).toBe(true);
    }

    fireEvent.click(screen.getByRole("button", { name: "复制轨迹标识" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(TRACE_ID));
  });

  it("流内错误给出中文提示，不直接呈现后端的英文文案", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(frame("error", { code: "STREAM_ERROR", message: "Stream interrupted" })),
        );
        controller.close();
      },
    });
    fetchMock.mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    renderPanel();
    openDebugTab();
    typeQuery();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("回答生成中断，请重试");
    expect(alert.textContent).not.toContain("Stream interrupted");
  });

  it("准备阶段失败时给出可理解的提示且不产生轨迹标识", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: "AGENT_API_KEY_MISSING", message: "Agent has no API key" },
        }),
        { status: 409, headers: { "content-type": "application/json" } },
      ),
    );

    renderPanel();
    openDebugTab();
    typeQuery();

    expect((await screen.findByRole("alert")).textContent).toContain(
      "该智能体缺少上游访问密钥，无法发起调试",
    );
    expect(screen.queryByText(/^Trace:/)).toBeNull();
  });
});
