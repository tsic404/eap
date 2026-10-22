import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ToastAction, ToastOptions } from "@/lib/toast-bus";

import { renderWithIntl } from "../test-utils";

const {
  pushMock,
  toastMock,
  mutateMock,
  createConversationMock,
  deleteConversationMock,
  restoreConversationMock,
  conversationsMock,
  agentsMock,
} = vi.hoisted(() => ({
  pushMock: vi.fn(),
  toastMock: vi.fn(),
  mutateMock: vi.fn(),
  createConversationMock: vi.fn(),
  deleteConversationMock: vi.fn(),
  restoreConversationMock: vi.fn(),
  conversationsMock: vi.fn(),
  agentsMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@/lib/use-conversations", () => ({
  useConversations: conversationsMock,
}));

vi.mock("@/lib/use-agents", () => ({
  useAllAgents: agentsMock,
}));

vi.mock("@/lib/conversation-service", () => ({
  createConversation: createConversationMock,
  deleteConversation: deleteConversationMock,
  restoreConversation: restoreConversationMock,
}));

vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({ toast: toastMock }),
}));

import type { Agent } from "@/lib/agent-types";
import type { Conversation } from "@/lib/conversation-types";

import { ConversationListPage } from "./conversation-list-page";

function agentStub(overrides: Partial<Agent> = {}): Agent {
  return {
    agentId: "ag-1",
    tenantId: "tenant-1",
    difyAppId: "app-1",
    name: "测试智能体",
    description: null,
    type: "chat",
    category: null,
    icon: null,
    tags: [],
    status: "published",
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
    ...overrides,
  };
}

function conversationStub(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: "conv-1",
    title: "项目讨论",
    agentId: "ag-1",
    agentName: "测试智能体",
    status: "active",
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function conversationsState(items: Conversation[]) {
  conversationsMock.mockReturnValue({
    data: { items, nextCursor: null },
    error: undefined,
    isLoading: false,
    mutate: mutateMock,
  });
}

function agentsState(agents: Agent[]) {
  agentsMock.mockReturnValue({ data: agents, error: undefined, isLoading: false, mutate: vi.fn() });
}

async function openPicker() {
  fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
  return within(await screen.findByRole("dialog"));
}

describe("ConversationListPage delete and undo", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    conversationsState([conversationStub()]);
    agentsState([agentStub()]);
    deleteConversationMock.mockResolvedValue(undefined);
    restoreConversationMock.mockResolvedValue({ id: "conv-1" });
  });

  /** Delete the listed conversation and return the toast options it reported. */
  async function deleteConversation(): Promise<ToastOptions & { action: ToastAction }> {
    renderWithIntl(<ConversationListPage />);
    fireEvent.click(screen.getByRole("button", { name: "删除会话" }));
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));
    await waitFor(() => expect(toastMock).toHaveBeenCalled());
    return toastMock.mock.calls[0][0];
  }

  // C-104 / TC-conv-03: the deletion is reversible, so neither the confirmation
  // nor the result may present it as final.
  it("keeps the confirmation honest about the undo instead of promising a one-way delete", async () => {
    renderWithIntl(<ConversationListPage />);
    fireEvent.click(screen.getByRole("button", { name: "删除会话" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/确定要删除「项目讨论」/)).toBeTruthy();
    expect(dialog.textContent).not.toContain("不可恢复");
    expect(dialog.textContent).toContain("撤销");
  });

  it("offers the undo in the success prompt and restores the conversation", async () => {
    const options = await deleteConversation();

    expect(deleteConversationMock).toHaveBeenCalledWith("conv-1");
    expect(options).toMatchObject({ type: "success", title: "会话已删除" });
    expect(options.action).toMatchObject({ label: "撤销" });

    options.action.onClick();

    await waitFor(() =>
      expect(restoreConversationMock).toHaveBeenCalledWith("conv-1"),
    );
    await waitFor(() => expect(mutateMock).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(toastMock).toHaveBeenLastCalledWith({
        type: "success",
        title: "会话已恢复",
      }),
    );
  });

  // A failed undo must not leave the list claiming a state the server no longer
  // holds (e.g. the conversation was already restored elsewhere).
  it("reports a failed undo and re-syncs the list", async () => {
    restoreConversationMock.mockRejectedValue(new Error("404"));

    const options = await deleteConversation();
    mutateMock.mockClear();
    options.action.onClick();

    await waitFor(() =>
      expect(toastMock).toHaveBeenLastCalledWith({
        type: "error",
        title: "撤销失败，请重试",
      }),
    );
    await waitFor(() => expect(mutateMock).toHaveBeenCalled());
  });
});

describe("ConversationListPage 新建会话入口", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    conversationsState([]);
    agentsState([agentStub()]);
  });

  it("在空列表与有列表两种状态下都呈现可操作的新建入口", () => {
    const empty = renderWithIntl(<ConversationListPage />);
    expect(
      (screen.getByRole("button", { name: "新建会话" }) as HTMLButtonElement).disabled,
    ).toBe(false);
    empty.unmount();

    conversationsState([conversationStub()]);
    renderWithIntl(<ConversationListPage />);
    expect(
      (screen.getByRole("button", { name: "新建会话" }) as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(screen.getByText("项目讨论")).toBeTruthy();
  });

  it("候选只含已发布智能体，选定后创建会话并进入其聊天窗口", async () => {
    agentsState([
      agentStub(),
      agentStub({ agentId: "ag-draft", name: "草稿智能体", status: "draft" }),
    ]);
    createConversationMock.mockResolvedValue(conversationStub({ id: "conv-new", title: null }));

    renderWithIntl(<ConversationListPage />);
    const picker = await openPicker();

    expect(picker.getByRole("button", { name: /测试智能体/ })).toBeTruthy();
    expect(picker.queryByText("草稿智能体")).toBeNull();

    fireEvent.click(picker.getByRole("button", { name: /测试智能体/ }));

    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith("/user/conversations/conv-new"),
    );
    expect(createConversationMock).toHaveBeenCalledWith("ag-1");
  });

  // C-123: with no published agent in the tenant the picker is reachable but
  // offers nothing to create, so the empty state must be shown instead.
  it("租户无已发布智能体时选择面呈现空态且无法创建", async () => {
    agentsState([]);

    renderWithIntl(<ConversationListPage />);
    const picker = await openPicker();

    expect(picker.getByText("暂无已发布的智能体")).toBeTruthy();
    expect(picker.queryByRole("button", { name: /测试智能体/ })).toBeNull();
    expect(createConversationMock).not.toHaveBeenCalled();
  });

  it("创建进行中重复触发只发起一次创建", async () => {
    const gate = Promise.withResolvers<Conversation>();
    createConversationMock.mockImplementation(() => gate.promise);

    renderWithIntl(<ConversationListPage />);
    const picker = await openPicker();
    const agentButton = picker.getByRole("button", { name: /测试智能体/ });

    fireEvent.click(agentButton);
    fireEvent.click(agentButton);

    expect(createConversationMock).toHaveBeenCalledTimes(1);

    gate.resolve(conversationStub({ id: "conv-new", title: null }));
    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith("/user/conversations/conv-new"),
    );
  });

  it("创建失败时停留原页，提示可关闭且选择面仍可再次尝试", async () => {
    createConversationMock.mockRejectedValue(new Error("boom"));

    renderWithIntl(<ConversationListPage />);
    const picker = await openPicker();

    fireEvent.click(picker.getByRole("button", { name: /测试智能体/ }));

    const alert = await picker.findByRole("alert");
    expect(alert.textContent).toContain("新建会话失败，请重试");
    expect(pushMock).not.toHaveBeenCalled();
    // The picker stays mounted so the user can retry immediately.
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(picker.getByRole("button", { name: /测试智能体/ })).toBeTruthy();

    // The prompt itself is closable.
    fireEvent.click(picker.getByRole("button", { name: "关闭错误提示" }));
    await waitFor(() => expect(picker.queryByRole("alert")).toBeNull());
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});
