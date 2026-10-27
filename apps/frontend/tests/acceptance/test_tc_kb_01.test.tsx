/**
 * TC-kb-01 — creating a knowledge base shows the success toast and adds a card
 * carrying name, description, type, document count, chunk count and the status
 * badge; a duplicate name is blocked at the name field with the contract copy.
 * The page runs against the real SWR hook and service layer; only the shared
 * axios instance is stubbed.
 */
import { fireEvent, screen, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import { RoleProvider } from "@/components/auth/role-context";
import type { ApiEnvelope } from "@/lib/auth-types";
import type {
  KnowledgeBase,
  KnowledgeBaseListResponse,
} from "@/lib/knowledge-types";

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("@/lib/http-client", () => ({
  apiClient: { get: getMock, post: postMock },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

import { KnowledgeListPage } from "@/components/knowledge/knowledge-list-page";
import { renderWithIntl } from "@/components/test-utils";

export const TC_ID = "TC-kb-01";

const EXISTING: KnowledgeBase = {
  kb_id: "kb-1",
  name: "产品手册",
  description: "产品说明与 FAQ",
  type: "business",
  indexing_status: "ready",
  doc_count: 3,
  chunk_count: 42,
  created_at: "2026-01-01T00:00:00Z",
};

const CREATED: KnowledgeBase = {
  kb_id: "kb-2",
  name: "探针知识库",
  description: "探针描述",
  type: "business",
  indexing_status: "ready",
  doc_count: 0,
  chunk_count: 0,
  created_at: "2026-10-27T00:00:00Z",
};

function envelope<T>(data: T): { data: ApiEnvelope<T> } {
  return { data: { code: 0, data, message: "" } };
}

function kbPage(items: KnowledgeBase[]): {
  data: ApiEnvelope<KnowledgeBaseListResponse>;
} {
  return envelope({ items, total: items.length });
}

function duplicateNameError(): unknown {
  return {
    isAxiosError: true,
    response: {
      status: 409,
      data: { error: { code: "KB_NAME_EXISTS", message: "知识库名称已存在" } },
    },
    message: "Request failed",
  };
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
        <RoleProvider role="platform_admin">
          <KnowledgeListPage />
        </RoleProvider>
      </ToastProvider>
    </SWRConfig>,
  );
}

async function openCreateDialog() {
  fireEvent.click(screen.getAllByRole("button", { name: "新建知识库" })[0]);
  return screen.findByRole("dialog");
}

function fillCreateForm(dialog: HTMLElement, name: string, description: string) {
  fireEvent.change(within(dialog).getByLabelText("名称"), {
    target: { value: name },
  });
  fireEvent.change(within(dialog).getByLabelText("描述"), {
    target: { value: description },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "创建" }));
}

describe("TC-kb-01 create knowledge base", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("shows the success toast and a new card with all six elements", async () => {
    const items: KnowledgeBase[] = [EXISTING];
    getMock.mockImplementation(() => Promise.resolve(kbPage([...items])));
    postMock.mockImplementation(() => {
      items.push(CREATED);
      return Promise.resolve(envelope(CREATED));
    });

    renderPage();
    await screen.findByRole("heading", { name: EXISTING.name });

    const dialog = await openCreateDialog();
    fillCreateForm(dialog, CREATED.name, CREATED.description ?? "");

    expect(await screen.findByText("知识库已创建")).toBeTruthy();

    const heading = await screen.findByRole("heading", { name: CREATED.name });
    const card = heading.closest("div.rounded-lg") as HTMLElement;
    expect(card).not.toBeNull();
    expect(within(card).getByText(CREATED.description!)).toBeTruthy();
    expect(within(card).getByText("类型 业务")).toBeTruthy();
    expect(within(card).getByText("文档 0")).toBeTruthy();
    expect(within(card).getByText("分块 0")).toBeTruthy();
    expect(within(card).getByText("就绪")).toBeTruthy();
  });

  it("blocks a duplicate name at the name field without creating a card", async () => {
    getMock.mockImplementation(() => Promise.resolve(kbPage([EXISTING])));
    postMock.mockRejectedValue(duplicateNameError());

    renderPage();
    await screen.findByRole("heading", { name: EXISTING.name });

    const dialog = await openCreateDialog();
    fillCreateForm(dialog, EXISTING.name, "另一份描述");

    expect(await within(dialog).findByText("知识库名称已存在")).toBeTruthy();
    expect(within(dialog).getByLabelText("名称").getAttribute("aria-invalid")).toBe(
      "true",
    );
    expect(screen.queryByText("知识库已创建")).toBeNull();
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(1);
    expect(postMock).toHaveBeenCalledTimes(1);
  });
});
