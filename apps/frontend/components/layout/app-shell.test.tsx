import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("./header", () => ({
  Header: ({ onMenuToggle }: { onMenuToggle?: () => void }) => (
    <button type="button" onClick={onMenuToggle}>
      打开导航
    </button>
  ),
}));

import { AppShell } from "./app-shell";

describe("AppShell", () => {
  it("toggles the mobile drawer via the hamburger and close button", () => {
    render(
      <AppShell sidebar={<nav aria-label="工作区导航">导航</nav>}>
        <p>内容</p>
      </AppShell>,
    );

    // Only the always-rendered desktop sidebar is present before opening.
    expect(screen.getAllByLabelText("工作区导航")).toHaveLength(1);

    fireEvent.click(screen.getByText("打开导航"));

    // The drawer adds a second sidebar instance.
    expect(screen.getAllByLabelText("工作区导航")).toHaveLength(2);

    fireEvent.click(screen.getAllByLabelText("关闭导航")[0]);

    expect(screen.getAllByLabelText("工作区导航")).toHaveLength(1);
  });
});
