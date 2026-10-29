/**
 * Draft-restore boundary of the creation wizard.
 *
 * A saved draft outlives the assets it names: a knowledge base or tool can be
 * deleted between sessions. The picker only toggles rows it can see, so a
 * restored id that is no longer listed must not survive a successful load —
 * otherwise it cannot be deselected and the create call fails permanently.
 * A failed load is the opposite case: the draft's bindings must survive a
 * network hiccup untouched.
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
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

import { AgentCreateForm } from "./agent-create-form";
import { DEFAULT_AGENT_FORM_VALUES } from "@/lib/agent-validation";
import type { Tool } from "@/lib/agent-types";
import type { KnowledgeBase } from "@/lib/knowledge-types";

const DRAFT_KEY = "eap:agent-create-draft";

const TOOL: Tool = {
  tool_id: "tool-1",
  name: "天气查询",
  description: null,
  type: "http",
  status: "active",
};

const KNOWLEDGE_BASE: KnowledgeBase = {
  kb_id: "kb-1",
  name: "产品手册",
  description: null,
  type: "business",
  indexing_status: "ready",
  doc_count: 1,
  chunk_count: 3,
  created_at: "2026-01-01T00:00:00Z",
};

function seedDraft() {
  window.localStorage.setItem(
    DRAFT_KEY,
    JSON.stringify({
      ...DEFAULT_AGENT_FORM_VALUES,
      agentId: "travel-bot",
      name: "差旅助手",
      // `kb-gone` / `tool-gone` were deleted after the draft was saved.
      knowledgeBaseIds: ["kb-1", "kb-gone"],
      toolIds: ["tool-1", "tool-gone"],
    }),
  );
}

function previewRow(label: string): HTMLElement {
  const labelNode = screen.getByText(label, { exact: true });
  const row = labelNode.parentElement;
  if (!row) throw new Error(`preview row for ${label} has no container`);
  return row;
}

async function openPreview(expectStepError?: string) {
  renderWithIntl(
    <ToastProvider>
      <AgentCreateForm />
    </ToastProvider>,
  );
  // The draft already satisfies step 1 and step 2 has no required fields.
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  await screen.findByLabelText("系统 Prompt");
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  await screen.findByRole("heading", { name: "绑定知识库" });
  if (expectStepError) {
    await screen.findByText(expectStepError);
  }
  fireEvent.click(screen.getByRole("button", { name: "下一步" }));
  await screen.findByRole("button", { name: "创建智能体" });
}

async function submit() {
  createAgentMock.mockResolvedValue({ agentId: "travel-bot" });
  fireEvent.click(screen.getByRole("button", { name: "创建智能体" }));
  await waitFor(() => expect(createAgentMock).toHaveBeenCalledTimes(1));
  return createAgentMock.mock.calls[0][0] as {
    knowledgeBaseIds: string[];
    toolIds: string[];
  };
}

beforeEach(() => {
  window.localStorage.clear();
  createAgentMock.mockReset();
  listToolsMock.mockReset();
  listKnowledgeBasesMock.mockReset();
  pushMock.mockReset();
  seedDraft();
});

describe("draft-restored bindings", () => {
  it("drops ids the loaded lists no longer carry", async () => {
    listKnowledgeBasesMock.mockResolvedValue([KNOWLEDGE_BASE]);
    listToolsMock.mockResolvedValue([TOOL]);

    await openPreview();

    await waitFor(() =>
      expect(previewRow("绑定知识库").textContent).toContain("产品手册"),
    );
    expect(previewRow("绑定知识库").textContent).not.toContain("kb-gone");
    expect(previewRow("绑定工具").textContent).toContain("天气查询");
    expect(previewRow("绑定工具").textContent).not.toContain("tool-gone");

    const payload = await submit();
    expect(payload.knowledgeBaseIds).toEqual(["kb-1"]);
    expect(payload.toolIds).toEqual(["tool-1"]);
  });

  it("keeps the restored ids when a list fails to load", async () => {
    listKnowledgeBasesMock.mockRejectedValue(new Error("network down"));
    listToolsMock.mockRejectedValue(new Error("network down"));

    await openPreview("知识库列表加载失败");

    expect(previewRow("绑定知识库").textContent).toContain("kb-1");
    expect(previewRow("绑定工具").textContent).toContain("tool-1");

    const payload = await submit();
    expect(payload.knowledgeBaseIds).toEqual(["kb-1", "kb-gone"]);
    expect(payload.toolIds).toEqual(["tool-1", "tool-gone"]);
  });
});
