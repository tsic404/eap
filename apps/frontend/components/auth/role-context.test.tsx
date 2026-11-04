import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { UserRole } from "@/lib/roles";

import { renderWithIntl } from "../test-utils";
import { WorkspaceEntry } from "../layout/workspace-entry";
import { RoleProvider, SessionRoleProvider, useRole } from "./role-context";

vi.mock("next/navigation", () => ({
  usePathname: () => "/user",
}));

/** A role-driven surface, as the top bar renders it. */
function Entry() {
  return <WorkspaceEntry role={useRole()} />;
}

function renderEntry(documentRole: UserRole, sessionRole: UserRole | null) {
  return renderWithIntl(
    <SessionRoleProvider role={sessionRole}>
      <RoleProvider role={documentRole}>
        <Entry />
      </RoleProvider>
    </SessionRoleProvider>,
  );
}

describe("RoleProvider", () => {
  it("keeps the document's cookie role until the session answers", () => {
    renderEntry("auditor", null);

    expect(screen.getByRole("link", { name: "管理端" }).getAttribute("href")).toBe(
      "/admin/run-logs",
    );
  });

  it("gives a cookie-less document the session's role once the profile answers", () => {
    // SSO lands on the user workspace before the browser has written
    // `eap_role`: the document renders as an employee and only the profile
    // read knows the account's own role.
    const { rerender } = renderEntry("employee", null);

    expect(screen.queryByRole("link", { name: "管理端" })).toBeNull();

    rerender(
      <SessionRoleProvider role="agent_admin">
        <RoleProvider role="employee">
          <Entry />
        </RoleProvider>
      </SessionRoleProvider>,
    );

    expect(screen.getByRole("link", { name: "管理端" }).getAttribute("href")).toBe("/admin");
  });

  it("hides the entry when the session's role is the least privilege", () => {
    renderEntry("platform_admin", "employee");

    expect(screen.queryByRole("link", { name: "管理端" })).toBeNull();
    expect(screen.queryByRole("link", { name: "用户工作区" })).toBeNull();
  });
});
