import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const { replaceMock, useAuthMock } = vi.hoisted(() => ({
  replaceMock: vi.fn(),
  useAuthMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

vi.mock("./auth-context", () => ({
  useAuth: () => useAuthMock(),
}));

import { renderWithIntl } from "../test-utils";
import { AuthGuard } from "./auth-guard";

describe("AuthGuard", () => {
  it("shows the callback loading state while auth resolves", () => {
    useAuthMock.mockReturnValue({ isLoading: true, isAuthenticated: false });
    renderWithIntl(
      <AuthGuard>
        <div>protected</div>
      </AuthGuard>,
    );

    expect(screen.getByText("正在完成登录…")).toBeTruthy();
    expect(screen.queryByText("protected")).toBeNull();
  });

  it("redirects to login when unauthenticated", () => {
    replaceMock.mockReset();
    useAuthMock.mockReturnValue({ isLoading: false, isAuthenticated: false });
    renderWithIntl(
      <AuthGuard>
        <div>protected</div>
      </AuthGuard>,
    );

    expect(replaceMock).toHaveBeenCalledWith("/login");
  });

  it("renders children when authenticated", () => {
    useAuthMock.mockReturnValue({ isLoading: false, isAuthenticated: true });
    renderWithIntl(
      <AuthGuard>
        <div>protected</div>
      </AuthGuard>,
    );

    expect(screen.getByText("protected")).toBeTruthy();
  });
});
