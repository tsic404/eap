import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AdminRouteGuard } from "./admin-route-guard";
import { RoleProvider } from "./role-context";

const ALLOWED_ROLES = [
  "platform_admin",
  "agent_admin",
  "knowledge_admin",
  "auditor",
] as const;

describe("AdminRouteGuard", () => {
  it("blocks employees and shows the friendly permission notice", () => {
    render(
      <RoleProvider role="employee">
        <AdminRouteGuard>
          <p>管理端内容</p>
        </AdminRouteGuard>
      </RoleProvider>,
    );

    expect(
      screen.getByText(/您没有管理权限。如需访问管理端，请联系平台管理员授予 agent_admin/),
    ).toBeTruthy();
    expect(screen.queryByText("管理端内容")).toBeNull();
  });

  it.each(ALLOWED_ROLES)("renders admin content for %s", (role) => {
    render(
      <RoleProvider role={role}>
        <AdminRouteGuard>
          <p>管理端内容</p>
        </AdminRouteGuard>
      </RoleProvider>,
    );

    expect(screen.getByText("管理端内容")).toBeTruthy();
    expect(screen.queryByText(/您没有管理权限/)).toBeNull();
  });
});
