import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RoleProvider } from "@/components/auth/role-context";

import { renderWithIntl } from "../test-utils";
import { AdminSidebar } from "./admin-sidebar";

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin/users",
}));

describe("AdminSidebar", () => {
  it("labels the members entry with the page's own name", () => {
    renderWithIntl(
      <RoleProvider role="platform_admin">
        <AdminSidebar />
      </RoleProvider>,
    );

    expect(screen.getByRole("link", { name: "成员管理" }).getAttribute("href")).toBe(
      "/admin/users",
    );
    expect(screen.queryByRole("link", { name: "用户管理" })).toBeNull();
  });
});
