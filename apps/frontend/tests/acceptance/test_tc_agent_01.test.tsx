/**
 * TC-agent-01 — the four-step creation wizard and its preview (AC-08).
 *
 * The wizard is the real component; only the two list sources and the create
 * call are stubbed, so the assertions cover what the admin observes: the
 * indicator names the four steps in order and highlights the current one,
 * every step advances, the preview repeats the entered values item by item —
 * including the bound knowledge bases — and a successful create shows the
 * success toast and lands on the new agent's detail page. The backend's 201
 * belongs to the agent-service suite; the browser-side replay lives in
 * `e2e/journeys/tc-agent-01-agent-create-wizard.spec.ts`.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/toast";
import { renderWithIntl } from "@/components/test-utils";

const { createAgentMock, listToolsMock, listKnowledgeBasesMock, pushMock } =
  vi.hoisted(() => ({
    createAgentMock: vi.fn(),
    listToolsMock: vi.fn(),
    listKnowledgeBasesMock: vi.fn(),
    pushMock: vi.fn(),
  }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
}));

import * as knowledgeServiceModule from "@/lib/knowledge-service";
import * as platformServiceModule from "@/lib/platform-service";

vi.mock("@/lib/platform-service", async (importOriginal) => ({
  ...(await importOriginal<typeof platformServiceModule>()),
  createAgent: createAgentMock,
  listTools: listToolsMock,
}));

vi.mock("@/lib/knowledge-service", async (importOriginal) => ({
  ...(await importOriginal<typeof knowledgeServiceModule>()),
  listAllKnowledgeBases: listKnowledgeBasesMock,
}));

import { AgentCreateForm } from "@/components/agents/agent-create-form";
import type { Tool } from "@/lib/agent-types";
import type { KnowledgeBase } from "@/lib/knowledge-types";

export const TC_ID = "TC-agent-01";

const TOOLS: Tool[] = [
  {
    tool_id: "tool-1",
    name: "天气查询",
    description: "按城市查询天气",
    type: "http",
    status: "active",
  },
  {
    tool_id: "tool-2",
    name: "汇率换算",
    description: "按币种换算金额",
    type: "http",
    status: "active",
  },
];

const KNOWLEDGE_BASES: KnowledgeBase[] = [
  {
    kb_id: "kb-1",
    name: "产品手册",
    description: "客服问答语料",
    type: "business",
    indexing_status: "ready",
    doc_count: 2,
    chunk_count: 12,
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    kb_id: "kb-2",
    name: "售后政策",
    description: null,
    type: "business",
    indexing_status: "ready",
    doc_count: 1,
    chunk_count: 4,
    created_at: "2026-01-02T00:00:00Z",
  },
];

function renderWizard() {
  return renderWithIntl(
    <ToastProvider>
      <AgentCreateForm />
    </ToastProvider>,
  );
}

function stepItems(): HTMLElement[] {
  const list = screen.getByRole("list", { name: "创建步骤" });
  return within(list).getAllByRole("listitem");
}

/** The preview row that carries `label`, read through its value cell. */
function previewRow(label: string): HTMLElement {
  const labelNode = screen.getByText(label, { exact: true });
  const row = labelNode.parentElement;
  if (!row) throw new Error(`preview row for ${label} has no container`);
  return row;
}

async function fillBasics() {
  fireEvent.change(screen.getByLabelText("标识（agentId）"), {
    target: { value: "travel-bot" },
  });
  fireEvent.change(screen.getByLabelText("名称"), {
    target: { value: "差旅助手" },
  });
  fireEvent.change(screen.getByLabelText("描述"), {
    target: { value: "处理差旅申请" },
  });
  fireEvent.change(screen.getByLabelText("分类"), { target: { value: "行政" } });
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  await screen.findByLabelText("系统 Prompt");
}

async function fillModelAndPrompt() {
  fireEvent.change(screen.getByLabelText("模型提供方"), {
    target: { value: "openai" },
  });
  fireEvent.change(screen.getByLabelText("模型 ID"), {
    target: { value: "gpt-4o-mini" },
  });
  fireEvent.change(screen.getByLabelText("系统 Prompt"), {
    target: { value: "你是差旅助手。" },
  });
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  await screen.findByRole("heading", { name: "绑定知识库" });
}

function selectResource(label: RegExp | string) {
  fireEvent.click(screen.getByRole("checkbox", { name: label }));
}

beforeEach(() => {
  window.localStorage.clear();
  createAgentMock.mockReset();
  listToolsMock.mockReset().mockResolvedValue(TOOLS);
  listKnowledgeBasesMock.mockReset().mockResolvedValue(KNOWLEDGE_BASES);
  pushMock.mockReset();
});

describe("TC-agent-01 creation wizard", () => {
  it("names the four steps in order and marks only the current one", async () => {
    renderWizard();

    expect(stepItems().map((item) => item.textContent)).toEqual([
      expect.stringContaining("基本信息"),
      expect.stringContaining("模型与提示"),
      expect.stringContaining("知识库/工具绑定"),
      expect.stringContaining("预览"),
    ]);
    expect(stepItems()[0].getAttribute("aria-current")).toBe("step");
    expect(stepItems()[1].getAttribute("aria-current")).toBeNull();

    await fillBasics();
    expect(stepItems()[0].getAttribute("aria-current")).toBeNull();
    expect(stepItems()[1].getAttribute("aria-current")).toBe("step");

    await fillModelAndPrompt();
    expect(stepItems()[2].getAttribute("aria-current")).toBe("step");

    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    await screen.findByRole("button", { name: "创建智能体" });
    expect(stepItems()[3].getAttribute("aria-current")).toBe("step");
  });

  it("binds knowledge bases and tools in the third step", async () => {
    renderWizard();
    await fillBasics();
    await fillModelAndPrompt();

    // Both binding groups live on the step named after them.
    expect(screen.getByRole("heading", { name: "绑定知识库" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "绑定工具" })).toBeTruthy();
    await screen.findByText("产品手册");
    expect(screen.getByText("售后政策")).toBeTruthy();

    selectResource(/产品手册/);
    selectResource(/天气查询/);

    expect(
      (screen.getByRole("checkbox", { name: /产品手册/ }) as HTMLInputElement).checked,
    ).toBe(true);
  });

  it("repeats every configured item in the preview", async () => {
    renderWizard();
    await fillBasics();
    await fillModelAndPrompt();

    selectResource(/产品手册/);
    selectResource(/售后政策/);
    selectResource(/天气查询/);
    selectResource(/汇率换算/);
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    await screen.findByRole("button", { name: "创建智能体" });

    expect(previewRow("标识").textContent).toContain("travel-bot");
    expect(previewRow("名称").textContent).toContain("差旅助手");
    expect(previewRow("描述").textContent).toContain("处理差旅申请");
    expect(previewRow("分类").textContent).toContain("行政");
    expect(previewRow("模型").textContent).toContain("openai / gpt-4o-mini");
    expect(previewRow("系统 Prompt").textContent).toContain("你是差旅助手。");
    // The knowledge-base row is the configuration the third step collected.
    expect(previewRow("绑定知识库").textContent).toContain("产品手册");
    expect(previewRow("绑定知识库").textContent).toContain("售后政策");
    expect(previewRow("绑定工具").textContent).toContain("天气查询");
    expect(previewRow("绑定工具").textContent).toContain("汇率换算");
  });

  it("submits the bound knowledge bases and lands on the detail page", async () => {
    createAgentMock.mockResolvedValue({ agentId: "travel-bot" });

    renderWizard();
    await fillBasics();
    await fillModelAndPrompt();

    selectResource(/产品手册/);
    selectResource(/天气查询/);
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(await screen.findByRole("button", { name: "创建智能体" }));

    await waitFor(() => expect(createAgentMock).toHaveBeenCalledTimes(1));
    expect(createAgentMock).toHaveBeenCalledWith(
      expect.objectContaining({
        agentId: "travel-bot",
        knowledgeBaseIds: ["kb-1"],
        toolIds: ["tool-1"],
      }),
    );
    expect(await screen.findByText("创建成功")).toBeTruthy();
    expect(pushMock).toHaveBeenCalledWith("/admin/agents/travel-bot");
  });
});
