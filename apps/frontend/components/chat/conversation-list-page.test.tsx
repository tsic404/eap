import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ToastAction, ToastOptions } from "@/lib/toast-bus";

import { renderWithIntl } from "../test-utils";

const { toastMock, mutateMock, deleteConversationMock, restoreConversationMock } =
  vi.hoisted(() => ({
    toastMock: vi.fn(),
    mutateMock: vi.fn(),
    deleteConversationMock: vi.fn(),
    restoreConversationMock: vi.fn(),
  }));

vi.mock("@/lib/use-conversations", () => ({
  useConversations: () => ({
    data: {
      items: [
        {
          id: "conv-1",
          title: "项目讨论",
          agentId: "ag-1",
          agentName: "测试智能体",
          status: "active",
          createdAt: "2026-01-01T00:00:00Z",
          updatedAt: "2026-01-01T00:00:00Z",
        },
      ],
      nextCursor: null,
    },
    error: undefined,
    isLoading: false,
    mutate: mutateMock,
  }),
}));

vi.mock("@/lib/conversation-service", () => ({
  deleteConversation: deleteConversationMock,
  restoreConversation: restoreConversationMock,
}));

vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({ toast: toastMock }),
}));

import { ConversationListPage } from "./conversation-list-page";

/** Delete the listed conversation and return the toast options it reported. */
async function deleteConversation(): Promise<ToastOptions & { action: ToastAction }> {
  renderWithIntl(<ConversationListPage />);
  fireEvent.click(screen.getByRole("button", { name: "删除会话" }));
  fireEvent.click(await screen.findByRole("button", { name: "删除" }));
  await waitFor(() => expect(toastMock).toHaveBeenCalled());
  return toastMock.mock.calls[0][0];
}

describe("ConversationListPage delete and undo", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    deleteConversationMock.mockResolvedValue(undefined);
    restoreConversationMock.mockResolvedValue({ id: "conv-1" });
  });

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
