import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { UserRole } from "@/lib/roles";

import { renderWithIntl } from "../test-utils";
import { WorkspaceEntry } from "./workspace-entry";

const { pathnameMock } = vi.hoisted(() => ({ pathnameMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: pathnameMock,
}));

/** Admin landing page fixed per role by C-15 / AC-49. */
const ADMIN_LANDINGS = [
  ["platform_admin", "/admin"],
  ["agent_admin", "/admin"],
  ["knowledge_admin", "/admin/knowledge"],
  ["auditor", "/admin/run-logs"],
] as const;

function renderEntry(role: UserRole, pathname: string) {
  pathnameMock.mockReturnValue(pathname);
  return renderWithIntl(<WorkspaceEntry role={role} />);
}

describe("WorkspaceEntry", () => {
  it.each(ADMIN_LANDINGS)(
    "%s reaches its own admin page from the user workspace",
    (role, landing) => {
      renderEntry(role, "/user");

      expect(screen.getByRole("link", { name: "管理端" }).getAttribute("href")).toBe(
        landing,
      );
      // A single navigation entry, not a two-item workspace switch whose
      // selection only rewrites its own label.
      expect(screen.queryByRole("menuitem")).toBeNull();
      expect(screen.queryByRole("button", { name: "管理端" })).toBeNull();
      expect(screen.queryByRole("button", { name: "用户工作区" })).toBeNull();
      expect(screen.queryByRole("button", { name: "管理工作区" })).toBeNull();
    },
  );

  it.each(ADMIN_LANDINGS)(
    "%s returns to the user workspace home from any admin page",
    (role) => {
      renderEntry(role, "/admin/run-logs");

      expect(
        screen.getByRole("link", { name: "用户工作区" }).getAttribute("href"),
      ).toBe("/user");
      expect(screen.queryByRole("link", { name: "管理端" })).toBeNull();
      expect(screen.queryByRole("menuitem")).toBeNull();
    },
  );

  it("gives employees no workspace entry in either area", () => {
    const { unmount } = renderEntry("employee", "/user");

    expect(screen.queryByRole("link", { name: "管理端" })).toBeNull();
    expect(screen.queryByRole("link", { name: "用户工作区" })).toBeNull();
    unmount();

    renderEntry("employee", "/admin");

    expect(screen.queryByRole("link", { name: "管理端" })).toBeNull();
    expect(screen.queryByRole("link", { name: "用户工作区" })).toBeNull();
  });
});
