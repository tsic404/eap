import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { useAuthMock } = vi.hoisted(() => ({
  useAuthMock: vi.fn(),
}));

vi.mock("@/components/auth/auth-context", () => ({
  useAuth: () => useAuthMock(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/user",
}));

import { Header } from "./header";

const user = {
  id: "u1",
  tenantId: "t1",
  email: "zhang@example.com",
  name: "张三",
  department: "研发部",
  role: "employee",
  avatarText: "张",
};

describe("Header", () => {
  it("shows the authenticated user and a logout menu item", () => {
    useAuthMock.mockReturnValue({ user, logout: vi.fn() });
    render(<Header />);

    fireEvent.click(screen.getByTitle("张三"));

    expect(screen.getByRole("menuitem", { name: "登出" })).toBeTruthy();
  });

  it("calls logout when the logout menu item is selected", () => {
    const logoutMock = vi.fn();
    useAuthMock.mockReturnValue({ user, logout: logoutMock });
    render(<Header />);

    fireEvent.click(screen.getByTitle("张三"));
    fireEvent.click(screen.getByRole("menuitem", { name: "登出" }));

    expect(logoutMock).toHaveBeenCalledTimes(1);
  });
});
