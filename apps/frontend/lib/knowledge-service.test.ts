import { beforeEach, describe, expect, it, vi } from "vitest";

const { listAllAgentsMock, getAgentMock, getMock } = vi.hoisted(() => ({
  listAllAgentsMock: vi.fn(),
  getAgentMock: vi.fn(),
  getMock: vi.fn(),
}));

import * as platformServiceModule from "./platform-service";

vi.mock("./http-client", () => ({
  apiClient: { get: getMock, post: vi.fn(), delete: vi.fn() },
}));

vi.mock("./platform-service", async (importOriginal) => ({
  // Keep the real error helpers; the duplicate-name predicate exercises them.
  ...(await importOriginal<typeof platformServiceModule>()),
  listAllAgents: listAllAgentsMock,
  getAgent: getAgentMock,
}));

import type { Agent, AgentDetail } from "./agent-types";
import type { KnowledgeBase } from "./knowledge-types";
import {
  isKbNameExistsError,
  listAgentsBoundToKnowledge,
  listAllKnowledgeBases,
} from "./knowledge-service";

function envelope<T>(data: T) {
  return { code: 0, data, message: "" };
}

function knowledgeBaseStub(kbId: string): KnowledgeBase {
  return {
    kb_id: kbId,
    name: kbId,
    description: null,
    type: "business",
    indexing_status: "ready",
    doc_count: 0,
    chunk_count: 0,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function axiosError(status: number, data?: unknown): unknown {
  return { isAxiosError: true, response: { status, data }, message: "Request failed" };
}

function agentStub(agentId: string, name: string): Agent {
  return {
    agentId,
    tenantId: "tenant-1",
    difyAppId: "app-1",
    name,
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
  };
}

function detailStub(agent: Agent, kbIds: string[]): AgentDetail {
  return {
    ...agent,
    boundKnowledge: kbIds.map((kbId) => ({ kbId, name: null })),
    boundTools: [],
    recentLogs: [],
    conversationCount: 0,
  };
}

beforeEach(() => {
  listAllAgentsMock.mockReset();
  getAgentMock.mockReset();
  getMock.mockReset();
});

describe("listAllKnowledgeBases", () => {
  it("walks every page when the tenant has more than one page of knowledge bases", async () => {
    const total = 250;
    getMock.mockImplementation(
      async (_url: string, config: { params: { page: number; limit: number } }) => {
        const { page, limit } = config.params;
        const start = (page - 1) * limit;
        const count = Math.min(limit, total - start);
        const items = Array.from({ length: count }, (_, index) =>
          knowledgeBaseStub(`kb-${start + index}`),
        );
        return { data: envelope({ items, total }) };
      },
    );

    const result = await listAllKnowledgeBases();

    expect(result).toHaveLength(total);
    expect(result[0].kb_id).toBe("kb-0");
    expect(result[total - 1].kb_id).toBe(`kb-${total - 1}`);
    expect(
      getMock.mock.calls.map((call) => [
        call[1].params.page,
        call[1].params.limit,
      ]),
    ).toEqual([
      [1, 100],
      [2, 100],
      [3, 100],
    ]);
  });

  it("returns the first page without extra calls when everything fits one page", async () => {
    getMock.mockResolvedValueOnce({
      data: envelope({ items: [knowledgeBaseStub("kb-1")], total: 1 }),
    });

    const result = await listAllKnowledgeBases();

    expect(result).toHaveLength(1);
    expect(getMock).toHaveBeenCalledTimes(1);
    expect(getMock.mock.calls[0][1].params).toEqual({ page: 1, limit: 100 });
  });
});

describe("listAgentsBoundToKnowledge", () => {
  it("returns only the names of agents bound to the knowledge base", async () => {
    const bound = agentStub("a1", "天气助手");
    const unbound = agentStub("a2", "客服助手");
    listAllAgentsMock.mockResolvedValue([bound, unbound]);
    getAgentMock.mockImplementation((agentId: string) =>
      Promise.resolve(
        agentId === "a1" ? detailStub(bound, ["kb-1"]) : detailStub(unbound, []),
      ),
    );

    await expect(listAgentsBoundToKnowledge("kb-1")).resolves.toEqual([
      "天气助手",
    ]);
  });

  it("rejects when an agent detail request fails", async () => {
    listAllAgentsMock.mockResolvedValue([agentStub("a1", "天气助手")]);
    getAgentMock.mockRejectedValue(new Error("network down"));

    await expect(listAgentsBoundToKnowledge("kb-1")).rejects.toThrow(
      "network down",
    );
  });
});

describe("isKbNameExistsError", () => {
  it("matches only the 409 duplicate-name conflict", () => {
    expect(
      isKbNameExistsError(axiosError(409, { error: { code: "KB_NAME_EXISTS" } })),
    ).toBe(true);
    expect(
      isKbNameExistsError(axiosError(409, { error: { code: "CONFLICT" } })),
    ).toBe(false);
    expect(
      isKbNameExistsError(axiosError(500, { error: { code: "KB_NAME_EXISTS" } })),
    ).toBe(false);
    expect(isKbNameExistsError(new Error("network down"))).toBe(false);
  });
});
