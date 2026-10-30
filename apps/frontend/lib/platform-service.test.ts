import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { getMock, postMock, deleteMock, refreshMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  deleteMock: vi.fn(),
  refreshMock: vi.fn(),
}));

vi.mock("./http-client", () => ({
  apiClient: {
    get: getMock,
    post: postMock,
    delete: deleteMock,
  },
}));

vi.mock("./refresh-channel", () => ({
  refreshAccessToken: refreshMock,
}));

import type { Agent } from "./agent-types";
import { extractApiErrorCode } from "./api-error";
import {
  deleteAgent,
  extractApiErrorMessage,
  isConflictError,
  isDuplicateAgentError,
  isInvalidStateError,
  listAllAgents,
  offlineAgent,
  postAgentDebug,
  publishAgent,
} from "./platform-service";
import { setAccessToken } from "./token-store";

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

  it("offlines with the current version", async () => {
    postMock.mockResolvedValueOnce({ data: envelope(agentStub("bot-1")) });

    await offlineAgent("bot-1", 3);

    expect(postMock).toHaveBeenCalledWith("/agents/bot-1/offline", { version: 3 });
  });

  it("deletes via DELETE", async () => {
    deleteMock.mockResolvedValueOnce({ data: envelope(null) });

    await deleteAgent("bot-1");

    expect(deleteMock).toHaveBeenCalledWith("/agents/bot-1");
  });
});

describe("postAgentDebug", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    refreshMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    setAccessToken("stale-token");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it("renews an expired access token and replays the turn once", async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(
        new Response(null, { status: 200, headers: { "content-type": "text/event-stream" } }),
      );
    refreshMock.mockResolvedValueOnce("fresh-token");

    const response = await postAgentDebug("bot-1", "你好", new AbortController().signal);

    expect(response.status).toBe(200);
    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer stale-token");
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe("Bearer fresh-token");
  });

  it("surfaces the 401 when the session cannot be renewed", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));
    refreshMock.mockResolvedValueOnce(null);

    const response = await postAgentDebug("bot-1", "你好", new AbortController().signal);

    expect(response.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("error helpers", () => {
  it("detects a 409 optimistic-lock conflict by error code", () => {
    expect(isConflictError(axiosError(409, { error: { code: "CONFLICT" } }))).toBe(true);
    expect(isConflictError(axiosError(409, { error: { code: "INVALID_STATE" } }))).toBe(false);
    expect(isConflictError(axiosError(409))).toBe(false);
    expect(isConflictError(axiosError(400))).toBe(false);
    expect(isConflictError(new Error("nope"))).toBe(false);
  });

  it("detects a 409 invalid-state rejection by error code", () => {
    expect(isInvalidStateError(axiosError(409, { error: { code: "INVALID_STATE" } }))).toBe(true);
    expect(isInvalidStateError(axiosError(409, { error: { code: "CONFLICT" } }))).toBe(false);
    // Auth reuses INVALID_STATE at 400, so the status must still match.
    expect(isInvalidStateError(axiosError(400, { error: { code: "INVALID_STATE" } }))).toBe(false);
  });

  it("detects a duplicate agent id conflict by error code", () => {
    expect(isDuplicateAgentError(axiosError(409, { error: { code: "DUPLICATE_AGENT_ID" } }))).toBe(
      true,
    );
    expect(isDuplicateAgentError(axiosError(409, { error: { code: "CONFLICT" } }))).toBe(false);
  });

  it("extracts the machine-readable error code", () => {
    expect(extractApiErrorCode(axiosError(409, { error: { code: "INVALID_STATE" } }))).toBe(
      "INVALID_STATE",
    );
    expect(extractApiErrorCode(axiosError(409))).toBeUndefined();
    expect(extractApiErrorCode(new Error("nope"))).toBeUndefined();
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
