import { describe, expect, it } from "vitest";

import type { Agent } from "./agent-types";
import { deriveCategories, filterAgents } from "./agent-filter";

function agent(partial: Partial<Agent> & { agentId: string }): Agent {
  return {
    tenantId: "t1",
    difyAppId: "app1",
    name: partial.agentId,
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
    createdAt: "2026-09-01T00:00:00Z",
    updatedAt: "2026-09-01T00:00:00Z",
    ...partial,
  };
}

const AGENTS: Agent[] = [
  agent({ agentId: "a", name: "客服助手", category: "客服", description: "处理售后" }),
  agent({ agentId: "b", name: "数据分析", category: "数据" }),
  agent({ agentId: "c", name: "代码审查", category: null }),
];

describe("deriveCategories", () => {
  it("returns distinct non-empty categories in first-seen order", () => {
    expect(deriveCategories(AGENTS)).toEqual(["客服", "数据"]);
  });

  it("returns an empty array when no agent has a category", () => {
    expect(deriveCategories([agent({ agentId: "x" })])).toEqual([]);
  });
});

describe("filterAgents", () => {
  it("returns all agents when search and category are unset", () => {
    expect(filterAgents(AGENTS, "", null)).toHaveLength(3);
  });

  it("matches name case-insensitively", () => {
    expect(filterAgents(AGENTS, "客服", null).map((a) => a.agentId)).toEqual(["a"]);
  });

  it("matches description", () => {
    expect(filterAgents(AGENTS, "售后", null).map((a) => a.agentId)).toEqual(["a"]);
  });

  it("filters by exact category", () => {
    expect(filterAgents(AGENTS, "", "数据").map((a) => a.agentId)).toEqual(["b"]);
  });

  it("combines search and category with AND", () => {
    expect(filterAgents(AGENTS, "分析", "客服")).toHaveLength(0);
  });
});
