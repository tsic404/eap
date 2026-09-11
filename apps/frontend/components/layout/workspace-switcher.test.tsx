import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkspaceSwitcher } from "./workspace-switcher";

describe("WorkspaceSwitcher", () => {
  it("updates the displayed workspace on select when uncontrolled", () => {
    render(<WorkspaceSwitcher />);

    fireEvent.click(screen.getByRole("button", { name: /用户工作区/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "管理工作区" }));

    const trigger = screen.getByRole("button", { name: /管理工作区/ });
    expect(trigger.textContent).toContain("管理工作区");
  });

  it("notifies onChange but stays controlled when value is provided", () => {
    const onChange = vi.fn();
    render(<WorkspaceSwitcher value="user" onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /用户工作区/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "管理工作区" }));

    expect(onChange).toHaveBeenCalledWith("admin");
    const trigger = screen.getByRole("button", { name: /用户工作区/ });
    expect(trigger.textContent).toContain("用户工作区");
  });
});
