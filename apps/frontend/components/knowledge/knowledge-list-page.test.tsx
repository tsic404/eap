import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithIntl } from "../test-utils";

const { toastMock, mutateMock, createKnowledgeBaseMock } = vi.hoisted(() => ({
  toastMock: vi.fn(),
  mutateMock: vi.fn(),
  createKnowledgeBaseMock: vi.fn(),
}));

import * as knowledgeServiceModule from "@/lib/knowledge-service";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/use-knowledge", () => ({
  useKnowledgeBases: () => ({
    data: { items: [], total: 0 },
    error: undefined,
    isLoading: false,
    mutate: mutateMock,
  }),
}));

vi.mock("@/lib/knowledge-service", async (importOriginal) => ({
  // Real isKbNameExistsError; only the network call is mocked.
  ...(await importOriginal<typeof knowledgeServiceModule>()),
  createKnowledgeBase: createKnowledgeBaseMock,
}));

vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({ toast: toastMock }),
}));

import { KnowledgeListPage } from "./knowledge-list-page";

function axiosError(status: number, data?: unknown): unknown {
  return {
    isAxiosError: true,
    response: { status, data },
    message: "Request failed",
  };
}

async function submitCreateForm() {
  renderWithIntl(<KnowledgeListPage />);
  fireEvent.click(screen.getAllByRole("button", { name: "新建知识库" })[0]);
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(screen.getByLabelText("名称"), {
    target: { value: "产品手册" },
  });
  fireEvent.click(screen.getByRole("button", { name: "创建" }));
  return dialog;
}

describe("KnowledgeListPage create dialog", () => {
  beforeEach(() => {
    toastMock.mockReset();
    mutateMock.mockReset();
    createKnowledgeBaseMock.mockReset();
  });

  it("echoes a duplicate-name 409 at the name field and keeps the dialog open", async () => {
    createKnowledgeBaseMock.mockRejectedValue(
      axiosError(409, {
        error: { code: "KB_NAME_EXISTS", message: "知识库名称已存在" },
      }),
    );

    const dialog = await submitCreateForm();

    expect(await within(dialog).findByText("知识库名称已存在")).toBeTruthy();
    expect(screen.getByLabelText("名称").getAttribute("aria-invalid")).toBe(
      "true",
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(toastMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("名称"), {
      target: { value: "产品手册 v2" },
    });
    expect(within(dialog).queryByText("知识库名称已存在")).toBeNull();
  });

  it("keeps non-duplicate failures on the toast path", async () => {
    createKnowledgeBaseMock.mockRejectedValue(
      axiosError(500, {
        error: { code: "INTERNAL_ERROR", message: "Internal server error" },
      }),
    );

    const dialog = await submitCreateForm();

    await waitFor(() => expect(toastMock).toHaveBeenCalledTimes(1));
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ type: "error", title: "创建失败" }),
    );
    expect(within(dialog).queryByText("知识库名称已存在")).toBeNull();
  });

  it("locks the name field and dialog close while a create is in flight", async () => {
    const createGate = Promise.withResolvers<never>();
    createKnowledgeBaseMock.mockImplementation(() => createGate.promise);

    const dialog = await submitCreateForm();
    const nameInput = screen.getByLabelText("名称");

    // Mid-flight: the submitted name is frozen and every close path is inert.
    await waitFor(() => expect(nameInput).toHaveProperty("disabled", true));
    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    fireEvent.click(dialog.querySelector('[aria-hidden="true"]')!);
    expect(screen.getByRole("dialog")).toBeTruthy();

    createGate.reject(
      axiosError(409, {
        error: { code: "KB_NAME_EXISTS", message: "知识库名称已存在" },
      }),
    );
    expect(await within(dialog).findByText("知识库名称已存在")).toBeTruthy();
    await waitFor(() => expect(nameInput).toHaveProperty("disabled", false));
  });
});
