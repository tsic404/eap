import { beforeEach, describe, expect, it, vi } from "vitest";

const { listAllAgentsMock, getAgentMock } = vi.hoisted(() => ({
  listAllAgentsMock: vi.fn(),
  getAgentMock: vi.fn(),
}));

vi.mock("./platform-service", () => ({
  listAllAgents: listAllAgentsMock,
  getAgent: getAgentMock,
}));

import type { Agent, AgentDetail } from "./agent-types";
import { listAgentsBoundToKnowledge } from "./knowledge-service";

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
  };
}

beforeEach(() => {
  listAllAgentsMock.mockReset();
  getAgentMock.mockReset();
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
