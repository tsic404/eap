import { beforeEach, describe, expect, it, vi } from "vitest";

const { patchMock } = vi.hoisted(() => ({
  patchMock: vi.fn(),
}));

vi.mock("./http-client", () => ({
  apiClient: { patch: patchMock, get: vi.fn(), post: vi.fn() },
}));

import { setMemberRole } from "./member-service";

const MEMBER = {
  id: "u-1",
  email: "alice@acme.com",
  name: "Alice",
  role: "auditor",
  status: "active",
  department: null,
  avatarText: null,
  lastActiveAt: null,
  createdAt: "2026-10-17T00:00:00Z",
  accountSource: "sso",
  mustChangePassword: false,
};

function envelope<T>(data: T) {
  return { code: 0, data, message: "" };
}

describe("setMemberRole", () => {
  beforeEach(() => {
    patchMock.mockReset();
  });

  it("patches the member's own path carrying only the role field", async () => {
    patchMock.mockResolvedValue({ data: envelope(MEMBER) });

    await expect(setMemberRole("u-1", "auditor")).resolves.toEqual(MEMBER);
    expect(patchMock).toHaveBeenCalledWith("/users/u-1", { role: "auditor" });
  });
});
