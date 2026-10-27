/**
 * TC-agent-06 — delete confirmation asks for the agent name and states the
 * related conversations (AC-13).
 *
 * The page, its dialog and the name comparison are the real components; only
 * the data source and the delete call are stubbed, so the assertions cover what
 * the admin observes: the confirm action stays disabled until the trimmed name
 * matches case-sensitively (C-49), and the dialog names the related
 * conversations when the detail reports any (AC-13). The browser-side replay
 * lives in `e2e/journeys/tc-agent-06-agent-delete-confirm.spec.ts`.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentDetail } from "@/lib/agent-types";
import * as platformServiceModule from "@/lib/platform-service";

import { ToastProvider } from "@/components/ui/toast";
import { renderWithIntl } from "@/components/test-utils";

const { deleteAgentMock, mutateMock, agentStub } = vi.hoisted(() => ({
  deleteAgentMock: vi.fn(),
  mutateMock: vi.fn(),
  agentStub: {
    agentId: "bot-1",
    tenantId: "tenant-1",
    difyAppId: "dify-app-1",
    name: "Support Bot",
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
    version: 1,
    createdBy: null,
    publishedAt: null,
    publishedBy: null,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
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
  useAgent: () => ({
    data: agentStub,
    error: undefined,
    isLoading: false,
    mutate: mutateMock,
  }),
}));

vi.mock("@/components/agents/agent-config-panel", () => ({
  AgentConfigPanel: () => null,
}));

vi.mock("@/lib/platform-service", async (importOriginal) => {
  const actual = await importOriginal<typeof platformServiceModule>();
  return { ...actual, deleteAgent: deleteAgentMock };
});

import { AgentDetailPage } from "@/components/agents/agent-detail-page";

export const TC_ID = "TC-agent-06";

function renderPage() {
  return renderWithIntl(
    <ToastProvider>
      <AgentDetailPage />
    </ToastProvider>,
  );
}

async function openDeleteDialog() {
  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  const dialog = await screen.findByRole("dialog");
  return {
    dialog,
    input: within(dialog).getByRole("textbox"),
    confirm: within(dialog).getByRole("button", { name: "删除" }) as HTMLButtonElement,
  };
}

beforeEach(() => {
  deleteAgentMock.mockReset();
  mutateMock.mockReset();
  agentStub.conversationCount = 0;
});

describe("TC-agent-06 delete confirmation", () => {
  it("keeps 删除 disabled until the trimmed name matches case-sensitively", async () => {
    deleteAgentMock.mockResolvedValueOnce(undefined);
    renderPage();

    const { input, confirm } = await openDeleteDialog();

    // Empty field: the action is disabled, not merely warned about.
    expect(confirm.disabled).toBe(true);

    // Case differences and partial names never enable it.
    fireEvent.change(input, { target: { value: "support bot" } });
    expect(confirm.disabled).toBe(true);
    fireEvent.change(input, { target: { value: "Support Bo" } });
    expect(confirm.disabled).toBe(true);

    // Surrounding whitespace is trimmed before the comparison (C-49).
    fireEvent.change(input, { target: { value: "  Support Bot  " } });
    expect(confirm.disabled).toBe(false);

    fireEvent.change(input, { target: { value: "Support Bot" } });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await waitFor(() => expect(deleteAgentMock).toHaveBeenCalledWith("bot-1"));
  });

  it("names the related conversations and what happens to them", async () => {
    agentStub.conversationCount = 3;
    renderPage();

    const { dialog } = await openDeleteDialog();

    expect(within(dialog).getByText(/有 3 个关联会话/)).toBeTruthy();
    expect(within(dialog).getByText(/跨全部用户统计/)).toBeTruthy();
    expect(within(dialog).getByText(/这些会话不会被删除，历史记录仍可查看/)).toBeTruthy();
  });

  it("omits the conversation notice when the agent has none", async () => {
    renderPage();

    const { dialog } = await openDeleteDialog();

    expect(within(dialog).queryByText(/关联会话/)).toBeNull();
  });
});
