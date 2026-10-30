/**
 * TC-agent-05 — the lifecycle badge walks 草稿(灰) → 测试中(蓝) → 已发布(绿) →
 * 已下线(红); a draft's 「开始测试」 entry acts without a confirmation step and
 * turns the badge 蓝; offline is terminal with no publish entry (AC-12, ADR-018).
 *
 * The detail page runs against the real SWR hook and the real service layer;
 * only the shared axios instance is stubbed. The colour family is judged through
 * the badge's semantic variant classes here — jsdom applies no stylesheet — and
 * replayed against the rendered hues in the browser suite
 * (`e2e/journeys/tc-agent-05-agent-lifecycle.spec.ts`). The employee-plaza half
 * of the criterion is backend-enforced visibility (C-34), not judged here.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import { describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { AgentDetail } from "@/lib/agent-types";
import type { ApiEnvelope } from "@/lib/auth-types";
import { renderWithIntl } from "@/components/test-utils";

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("@/lib/http-client", () => ({
  apiClient: { get: getMock, post: postMock },
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "bot-1" }),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { AgentDetailPage } from "@/components/agents/agent-detail-page";

export const TC_ID = "TC-agent-05";

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
  version: 2,
  createdBy: "user-1",
  publishedAt: null,
  publishedBy: null,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
  boundKnowledge: [],
  boundTools: [],
  conversationCount: 0,
  recentLogs: [],
};

interface StateExpectation {
  status: string;
  label: string;
  token: string;
  offers: string[];
  hides: string[];
}

const STATES: StateExpectation[] = [
  { status: "draft", label: "草稿", token: "bg-muted", offers: ["开始测试", "发布", "删除"], hides: ["下线"] },
  { status: "testing", label: "测试中", token: "bg-info-subtle", offers: ["发布", "删除"], hides: ["开始测试", "下线"] },
  { status: "published", label: "已发布", token: "bg-success-subtle", offers: ["下线", "删除"], hides: ["开始测试", "发布"] },
  { status: "offline", label: "已下线", token: "bg-danger-subtle", offers: ["删除"], hides: ["开始测试", "发布", "下线"] },
];

function envelope<T>(data: T): { data: ApiEnvelope<T> } {
  return { data: { code: 0, data, message: "" } };
}

function renderPage() {
  return renderWithIntl(
    <SWRConfig
      value={{
        provider: () => new Map(),
        dedupingInterval: 0,
        shouldRetryOnError: false,
      }}
    >
      <ToastProvider>
        <AgentDetailPage />
      </ToastProvider>
    </SWRConfig>,
  );
}

/**
 * The header badge. The config tab renders a second status badge for the same
 * agent, so the assertion is scoped to the heading's row.
 */
function headerBadge(label: string): HTMLElement {
  const heading = screen.getByRole("heading", { name: AGENT.name });
  return within(heading.parentElement as HTMLElement).getByText(label, { exact: true });
}

describe("TC-agent-05 lifecycle badges and entries", () => {
  it("moves a draft into testing on 开始测试 without a confirmation step", async () => {
    const server = { ...AGENT };
    getMock.mockImplementation(() => Promise.resolve(envelope({ ...server })));
    postMock.mockImplementation((url: string) => {
      if (url !== "/agents/bot-1/start-testing") {
        return Promise.reject(new Error(`unexpected POST ${url}`));
      }
      server.status = "testing";
      server.version += 1;
      return Promise.resolve(envelope({ ...server }));
    });

    renderPage();
    await waitFor(() => expect(headerBadge("草稿")).toBeTruthy());
    expect(headerBadge("草稿").className).toContain("bg-muted");

    fireEvent.click(screen.getByRole("button", { name: "开始测试" }));

    // ADR-018: no confirmation dialog — the click itself is the action.
    expect(screen.queryByRole("dialog")).toBeNull();

    await waitFor(() => expect(headerBadge("测试中")).toBeTruthy());
    expect(headerBadge("测试中").className).toContain("bg-info-subtle");
    expect(postMock).toHaveBeenCalledWith("/agents/bot-1/start-testing", {
      version: 2,
    });
  });

  it.each(STATES)(
    "renders $status as $label ($token) with its lifecycle entries",
    async ({ status, label, token, offers, hides }: StateExpectation) => {
      getMock.mockImplementation(() =>
        Promise.resolve(envelope({ ...AGENT, status })),
      );

      renderPage();
      await waitFor(() => expect(headerBadge(label)).toBeTruthy());
      expect(headerBadge(label).className).toContain(token);

      for (const entry of offers) {
        expect(screen.getByRole("button", { name: entry })).toBeTruthy();
      }
      for (const entry of hides) {
        expect(screen.queryByRole("button", { name: entry })).toBeNull();
      }
    },
  );
});
