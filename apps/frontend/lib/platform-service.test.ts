import { beforeEach, describe, expect, it, vi } from "vitest";

const { getMock, postMock, deleteMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  deleteMock: vi.fn(),
}));

vi.mock("./http-client", () => ({
  apiClient: {
    get: getMock,
    post: postMock,
    delete: deleteMock,
  },
}));

import type { Agent } from "./agent-types";
import {
  deleteAgent,
  extractApiErrorMessage,
  isConflictError,
  listAllAgents,
  offlineAgent,
  publishAgent,
} from "./platform-service";

function envelope<T>(data: T) {
  return { code: 0, data, message: "" };
}

function agentStub(agentId: string): Agent {
  return {
    agentId,
    tenantId: "t1",
    difyAppId: "app1",
    name: agentId,
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
  };
}

function axiosError(status: number, data?: unknown): unknown {
  return { isAxiosError: true, response: { status, data }, message: "Request failed" };
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  deleteMock.mockReset();
});

describe("listAllAgents", () => {
  it("fetches every page in parallel when total spans multiple pages", async () => {
    const total = 250;
    getMock.mockImplementation(async (_url: string, config: { params: { page: number; pageSize: number } }) => {
      const { page, pageSize } = config.params;
      const start = (page - 1) * pageSize;
      const count = Math.min(pageSize, total - start);
      const items = Array.from({ length: count }, (_, i) => agentStub(`agent-${start + i}`));
      return { data: envelope({ items, total, page, pageSize }) };
    });

    const result = await listAllAgents();

    expect(result).toHaveLength(total);
    expect(getMock).toHaveBeenCalledTimes(3);
    expect(getMock.mock.calls.map((call) => call[1].params.page)).toEqual([1, 2, 3]);
  });

  it("returns the first page without extra calls when total fits one page", async () => {
    getMock.mockResolvedValueOnce({
      data: envelope({ items: [agentStub("a")], total: 1, page: 1, pageSize: 100 }),
    });

    const result = await listAllAgents();

    expect(result).toHaveLength(1);
    expect(getMock).toHaveBeenCalledTimes(1);
  });
});

describe("agent lifecycle calls", () => {
  it("publishes with the current version", async () => {
    postMock.mockResolvedValueOnce({ data: envelope(agentStub("bot-1")) });

    const result = await publishAgent("bot-1", 3);

    expect(postMock).toHaveBeenCalledWith("/agents/bot-1/publish", { version: 3 });
    expect(result.agentId).toBe("bot-1");
  });

  it("offlines without a body", async () => {
    postMock.mockResolvedValueOnce({ data: envelope(agentStub("bot-1")) });

    await offlineAgent("bot-1");

    expect(postMock).toHaveBeenCalledWith("/agents/bot-1/offline");
  });

  it("deletes via DELETE", async () => {
    deleteMock.mockResolvedValueOnce({ data: envelope(null) });

    await deleteAgent("bot-1");

    expect(deleteMock).toHaveBeenCalledWith("/agents/bot-1");
  });
});

describe("error helpers", () => {
  it("detects a 409 optimistic-lock conflict", () => {
    expect(isConflictError(axiosError(409))).toBe(true);
    expect(isConflictError(axiosError(400))).toBe(false);
    expect(isConflictError(new Error("nope"))).toBe(false);
  });

  it("extracts the backend message from the error envelope", () => {
    expect(
      extractApiErrorMessage(axiosError(409, { error: { code: "CONFLICT", message: "Agent version conflict" } })),
    ).toBe("Agent version conflict");
  });

  it("falls back to the generic message for non-envelope errors", () => {
    expect(extractApiErrorMessage(new Error("network down"))).toBe("network down");
  });
});
