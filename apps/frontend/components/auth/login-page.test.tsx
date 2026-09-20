import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { useAuthMock } = vi.hoisted(() => ({
  useAuthMock: vi.fn(),
}));

vi.mock("./auth-context", () => ({
  useAuth: () => useAuthMock(),
}));

import { renderWithIntl } from "../test-utils";
import { LoginPage } from "./login-page";

describe("LoginPage", () => {
  it("starts SSO login for the default provider on submit", () => {
    const loginMock = vi.fn();
    useAuthMock.mockReturnValue({ login: loginMock, error: null });
    renderWithIntl(<LoginPage />);

    fireEvent.click(screen.getByRole("button", { name: "SSO 登录" }));

    expect(loginMock).toHaveBeenCalledWith("oidc");
  });

  it("shows the auth error state", () => {
    useAuthMock.mockReturnValue({ login: vi.fn(), error: "登录失败，请重试" });
    renderWithIntl(<LoginPage />);

    expect(screen.getByRole("alert").textContent).toBe("登录失败，请重试");
  });
});
