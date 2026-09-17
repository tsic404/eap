import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkspaceSwitcher } from "./workspace-switcher";

const ADMIN_ROLES = [
  "platform_admin",
  "agent_admin",
  "knowledge_admin",
  "auditor",
] as const;

describe("WorkspaceSwitcher", () => {
  it("shows only the user workspace for employee", () => {
    render(<WorkspaceSwitcher role="employee" />);

    fireEvent.click(screen.getByRole("button", { name: /用户工作区/ }));

    expect(screen.getByRole("menuitem", { name: "用户工作区" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "管理工作区" })).toBeNull();
  });

  it("defaults to the employee view when no role is provided", () => {
    render(<WorkspaceSwitcher />);

    fireEvent.click(screen.getByRole("button", { name: /用户工作区/ }));

    expect(screen.queryByRole("menuitem", { name: "管理工作区" })).toBeNull();
  });

  it.each(ADMIN_ROLES)("shows both workspaces for %s", (role) => {
    render(<WorkspaceSwitcher role={role} />);

    fireEvent.click(screen.getByRole("button", { name: /用户工作区/ }));

    expect(screen.getByRole("menuitem", { name: "管理工作区" })).toBeTruthy();
  });

  it("updates the displayed workspace on select when uncontrolled", () => {
    render(<WorkspaceSwitcher role="platform_admin" />);

    fireEvent.click(screen.getByRole("button", { name: /用户工作区/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "管理工作区" }));

    const trigger = screen.getByRole("button", { name: /管理工作区/ });
    expect(trigger.textContent).toContain("管理工作区");
  });

  it("notifies onChange but stays controlled when value is provided", () => {
    const onChange = vi.fn();
    render(<WorkspaceSwitcher role="platform_admin" value="user" onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /用户工作区/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "管理工作区" }));

    expect(onChange).toHaveBeenCalledWith("admin");
    const trigger = screen.getByRole("button", { name: /用户工作区/ });
    expect(trigger.textContent).toContain("用户工作区");
  });
});
