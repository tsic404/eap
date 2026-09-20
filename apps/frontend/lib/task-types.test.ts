import { describe, expect, it } from "vitest";

import { canTransition, isTaskAdminRole } from "./task-types";

describe("canTransition", () => {
  it("allows the user-driven approval edges", () => {
    expect(canTransition("pending", "approved")).toBe(true);
    expect(canTransition("pending", "rejected")).toBe(true);
    expect(canTransition("pending", "cancelled")).toBe(true);
    expect(canTransition("failed", "executing")).toBe(true);
  });

  it("rejects transitions from terminal statuses", () => {
    expect(canTransition("completed", "executing")).toBe(false);
    expect(canTransition("rejected", "approved")).toBe(false);
    expect(canTransition("cancelled", "approved")).toBe(false);
  });

  it("rejects unknown statuses", () => {
    expect(canTransition("bogus", "approved")).toBe(false);
  });
});

describe("isTaskAdminRole", () => {
  it("grants only platform_admin and agent_admin", () => {
    expect(isTaskAdminRole("platform_admin")).toBe(true);
    expect(isTaskAdminRole("agent_admin")).toBe(true);
    expect(isTaskAdminRole("knowledge_admin")).toBe(false);
    expect(isTaskAdminRole("auditor")).toBe(false);
    expect(isTaskAdminRole("employee")).toBe(false);
  });
});
