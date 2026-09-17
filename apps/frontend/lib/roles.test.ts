import { describe, expect, it } from "vitest";

import { canAccessAdmin, isUserRole, resolveRole, type UserRole } from "./roles";

const NON_EMPLOYEE_ROLES: UserRole[] = [
  "platform_admin",
  "agent_admin",
  "knowledge_admin",
  "auditor",
];

describe("canAccessAdmin", () => {
  it("denies employees from the admin workspace", () => {
    expect(canAccessAdmin("employee")).toBe(false);
  });

  it.each(NON_EMPLOYEE_ROLES)("allows %s into the admin workspace", (role) => {
    expect(canAccessAdmin(role)).toBe(true);
  });
});

describe("isUserRole", () => {
  it.each(["platform_admin", "agent_admin", "knowledge_admin", "auditor", "employee"])(
    "accepts the %s role",
    (role) => {
      expect(isUserRole(role)).toBe(true);
    },
  );

  it("rejects unknown values", () => {
    expect(isUserRole("superuser")).toBe(false);
    expect(isUserRole("")).toBe(false);
    expect(isUserRole(undefined)).toBe(false);
    expect(isUserRole(42)).toBe(false);
  });
});

describe("resolveRole", () => {
  it("returns a known role unchanged", () => {
    expect(resolveRole("platform_admin")).toBe("platform_admin");
  });

  it("falls back to the default role for unknown values", () => {
    expect(resolveRole("superuser")).toBe("employee");
    expect(resolveRole(undefined)).toBe("employee");
  });
});
