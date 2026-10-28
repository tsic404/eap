import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { useAuthMock, fetchAuthCapabilitiesMock, replaceMock } = vi.hoisted(() => ({
  useAuthMock: vi.fn(),
  fetchAuthCapabilitiesMock: vi.fn(),
  replaceMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

vi.mock("./auth-context", () => ({
  useAuth: () => useAuthMock(),
}));

vi.mock("@/lib/auth-service", () => ({
  fetchAuthCapabilities: () => fetchAuthCapabilitiesMock(),
}));

import { renderWithIntl } from "../test-utils";
import { LoginPage } from "./login-page";

/**
 * Let the capability read point resolve before asserting that something is
 * absent — otherwise the assertion passes merely because the fetch is pending.
 */
async function settleCapability(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

/** Links or buttons that would open a self-service sign-up. */
function registrationEntries(): HTMLElement[] {
  const interactive = [...screen.queryAllByRole("link"), ...screen.queryAllByRole("button")];
  return interactive.filter((element) => /注册|register|signup/i.test(element.textContent ?? ""));
}

beforeEach(() => {
  useAuthMock.mockReturnValue({ login: vi.fn(), error: null });
  fetchAuthCapabilitiesMock.mockResolvedValue({ localLoginEnabled: false });
});

describe("LoginPage enterprise identity entry", () => {
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

describe("LoginPage local account entry", () => {
  it("renders the email-and-password form when the capability read point reports it open", async () => {
    fetchAuthCapabilitiesMock.mockResolvedValue({ localLoginEnabled: true });
    renderWithIntl(<LoginPage />);

    await settleCapability();

    expect(screen.getByLabelText("邮箱")).toBeTruthy();
    expect(screen.getByLabelText("口令")).toBeTruthy();
    expect(screen.getByRole("button", { name: "登录" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "SSO 登录" })).toBeTruthy();
  });

  it("renders only the enterprise entry when the capability read point reports it closed", async () => {
    renderWithIntl(<LoginPage />);

    await settleCapability();

    expect(screen.getByRole("button", { name: "SSO 登录" })).toBeTruthy();
    expect(screen.queryByLabelText("邮箱")).toBeNull();
    expect(screen.queryByLabelText("口令")).toBeNull();
    expect(screen.queryByRole("button", { name: "登录" })).toBeNull();
  });

  it("keeps the account form hidden when the capability read point is unreachable", async () => {
    fetchAuthCapabilitiesMock.mockRejectedValue(new Error("backend down"));
    renderWithIntl(<LoginPage />);

    await settleCapability();

    expect(fetchAuthCapabilitiesMock).toHaveBeenCalled();
    expect(screen.queryByLabelText("邮箱")).toBeNull();
    expect(screen.getByRole("button", { name: "SSO 登录" })).toBeTruthy();
  });
});

describe("LoginPage self-service sign-up", () => {
  it("offers no sign-up entry while the capability is closed", async () => {
    renderWithIntl(<LoginPage />);

    await settleCapability();

    expect(registrationEntries()).toEqual([]);
    expect(screen.queryByText(/注册/)).toBeNull();
  });

  it("offers no sign-up entry while the capability is open", async () => {
    fetchAuthCapabilitiesMock.mockResolvedValue({ localLoginEnabled: true });
    renderWithIntl(<LoginPage />);

    await waitFor(() => expect(screen.getByLabelText("邮箱")).toBeTruthy());

    expect(registrationEntries()).toEqual([]);
    expect(screen.queryByText(/注册/)).toBeNull();
  });
});
