import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { AgentDetail } from "@/lib/agent-types";
import * as platformServiceModule from "@/lib/platform-service";

import { renderWithIntl } from "../test-utils";

const { publishMock, startTestingMock, mutateMock, agentStub } = vi.hoisted(() => ({
  publishMock: vi.fn(),
  startTestingMock: vi.fn(),
  mutateMock: vi.fn(),
  agentStub: {
    agentId: "bot-1",
    tenantId: "t1",
    difyAppId: "app-1",
    name: "客服助手",
    description: null,
    type: "chat",
    category: null,
    icon: null,
    tags: [],
    status: "draft",
    modelId: null,
    modelName: null,
    modelProvider: null,
    prompt: null,
    version: 2,
    createdBy: null,
    publishedAt: null,
    publishedBy: null,
    createdAt: "2026-09-01T00:00:00Z",
    updatedAt: "2026-09-01T00:00:00Z",
    boundKnowledge: [],
    boundTools: [],
    recentLogs: [],
    conversationCount: 0,
  } as AgentDetail,
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "bot-1" }),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

vi.mock("@/lib/use-agents", () => ({
  useAgent: () => ({ data: agentStub, error: undefined, isLoading: false, mutate: mutateMock }),
}));

vi.mock("@/components/agents/agent-config-panel", () => ({
  AgentConfigPanel: () => null,
}));

vi.mock("@/lib/platform-service", async (importOriginal) => {
  const actual = await importOriginal<typeof platformServiceModule>();
  return { ...actual, publishAgent: publishMock, startTestingAgent: startTestingMock };
});

import { AgentDetailPage } from "./agent-detail-page";

function axiosError(status: number, code: string, message: string): unknown {
  return {
    isAxiosError: true,
    response: { status, data: { error: { code, message } } },
    message: "Request failed",
  };
}

function renderPage() {
  return renderWithIntl(
    <ToastProvider>
      <AgentDetailPage />
    </ToastProvider>,
  );
}

/** Click the page's 发布 button, then the confirm dialog's 发布 button. */
async function publishViaDialog() {
  fireEvent.click(screen.getByRole("button", { name: "发布" }));
  const confirm = await screen.findByRole("dialog");
  fireEvent.click(within(confirm).getByRole("button", { name: "发布" }));
}

beforeEach(() => {
  publishMock.mockReset();
  startTestingMock.mockReset();
  mutateMock.mockReset();
  agentStub.status = "draft";
});

describe("AgentDetailPage lifecycle entries", () => {
  it("offers 开始测试 and 发布 while the agent is a draft", () => {
    renderPage();

    expect(screen.getByRole("button", { name: "开始测试" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "发布" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "下线" })).toBeNull();
  });

  it("offers 发布 while the agent is testing", () => {
    agentStub.status = "testing";
    renderPage();

    expect(screen.getByRole("button", { name: "发布" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "开始测试" })).toBeNull();
  });

  it("offers 下线 and no 发布 once published", () => {
    agentStub.status = "published";
    renderPage();

    expect(screen.getByRole("button", { name: "下线" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "发布" })).toBeNull();
    expect(screen.queryByRole("button", { name: "开始测试" })).toBeNull();
  });

  it("offers no publish entry at all once offline", () => {
    agentStub.status = "offline";
    renderPage();

    expect(screen.queryByRole("button", { name: "发布" })).toBeNull();
    expect(screen.queryByRole("button", { name: "下线" })).toBeNull();
    expect(screen.queryByRole("button", { name: "开始测试" })).toBeNull();
    expect(screen.getByRole("button", { name: "删除" })).toBeTruthy();
  });
});

describe("AgentDetailPage start-testing action", () => {
  it("starts testing on the click itself, using the current version", async () => {
    startTestingMock.mockResolvedValueOnce(agentStub);
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "开始测试" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(await screen.findByText("已开始测试")).toBeTruthy();
    expect(startTestingMock).toHaveBeenCalledWith("bot-1", 2);
    await waitFor(() => expect(mutateMock).toHaveBeenCalled());
  });

  it("keeps the version-conflict dialog for a 409 CONFLICT", async () => {
    startTestingMock.mockRejectedValueOnce(
      axiosError(409, "CONFLICT", "Agent version conflict"),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "开始测试" }));

    expect(await screen.findByText("版本冲突")).toBeTruthy();
  });

  it("reports a stale state without the conflict dialog for 409 INVALID_STATE", async () => {
    startTestingMock.mockRejectedValueOnce(
      axiosError(409, "INVALID_STATE", "Agent in status 'published' cannot enter testing"),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "开始测试" }));

    expect(await screen.findByText("只有草稿智能体可以开始测试")).toBeTruthy();
    expect(screen.queryByText("版本冲突")).toBeNull();
  });
});

describe("AgentDetailPage publish failures", () => {
  // 页面停留在刷新前的「草稿」，另一管理员已下线该智能体——发布入口仍在，后端以 409 拒绝。
  it("shows the offline message instead of the version-conflict dialog for 409 INVALID_STATE", async () => {
    publishMock.mockRejectedValueOnce(
      axiosError(409, "INVALID_STATE", "Agent in status 'offline' cannot be published"),
    );
    renderPage();

    await publishViaDialog();

    expect(await screen.findByText("该智能体已下线，不可发布")).toBeTruthy();
    expect(screen.queryByText("版本冲突")).toBeNull();
  });

  it("keeps the version-conflict dialog for an optimistic-lock 409 CONFLICT", async () => {
    publishMock.mockRejectedValueOnce(axiosError(409, "CONFLICT", "Agent version conflict"));
    renderPage();

    await publishViaDialog();

    expect(await screen.findByText("版本冲突")).toBeTruthy();
    expect(screen.queryByText("该智能体已下线，不可发布")).toBeNull();
  });

  it("falls back to the generic failure toast for a 404 NOT_FOUND", async () => {
    publishMock.mockRejectedValueOnce(axiosError(404, "NOT_FOUND", "Agent not found"));
    renderPage();

    await publishViaDialog();

    expect(await screen.findByText("操作失败")).toBeTruthy();
    expect(screen.queryByText("版本冲突")).toBeNull();
    expect(screen.queryByText("该智能体已下线，不可发布")).toBeNull();
  });
});
