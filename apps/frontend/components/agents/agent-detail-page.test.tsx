import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import type { AgentDetail } from "@/lib/agent-types";
import * as platformServiceModule from "@/lib/platform-service";

import { renderWithIntl } from "../test-utils";

const { publishMock, mutateMock, agentStub } = vi.hoisted(() => ({
  publishMock: vi.fn(),
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
    status: "offline",
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
  return { ...actual, publishAgent: publishMock };
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
  mutateMock.mockReset();
});

describe("AgentDetailPage publish failures", () => {
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
