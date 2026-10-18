import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import zhCN from "../../messages/zh-CN.json";

const { useAuthMock } = vi.hoisted(() => ({
  useAuthMock: vi.fn(),
}));

vi.mock("./auth-context", () => ({
  useAuth: () => useAuthMock(),
}));

import { renderWithIntl } from "../test-utils";
import { LocalLoginForm } from "./local-login-form";

function apiError(status: number, code: string): unknown {
  return {
    isAxiosError: true,
    response: { status, data: { error: { code } } },
    message: "Request failed",
  };
}

function fillCredentials(email = "carol@acme.com", password = "Local-Account-1") {
  fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("口令"), { target: { value: password } });
}

function submit() {
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
}

describe("LocalLoginForm submit", () => {
  it("submits the typed email and password", async () => {
    const loginWithPassword = vi.fn().mockResolvedValue(undefined);
    useAuthMock.mockReturnValue({ loginWithPassword });
    renderWithIntl(<LocalLoginForm />);

    fillCredentials();
    submit();

    await waitFor(() =>
      expect(loginWithPassword).toHaveBeenCalledWith("carol@acme.com", "Local-Account-1"),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("blocks a second submit while the first is in flight", async () => {
    const inFlight = Promise.withResolvers<void>();
    const loginWithPassword = vi.fn(() => inFlight.promise);
    useAuthMock.mockReturnValue({ loginWithPassword });
    renderWithIntl(<LocalLoginForm />);

    fillCredentials();
    submit();

    // The loading spinner joins the accessible name, so match on the label part.
    await waitFor(() => {
      const submitButton = screen.getByRole("button", { name: /登录/ });
      expect(submitButton).toHaveProperty("disabled", true);
    });
    expect(loginWithPassword).toHaveBeenCalledTimes(1);
  });
});

describe("LocalLoginForm error copy", () => {
  it.each([
    ["429 RATE_LIMITED login throttle", 429, "RATE_LIMITED", zhCN.login.errors.rateLimited],
    ["429 LOGIN_LOCKED lockout", 429, "LOGIN_LOCKED", zhCN.login.errors.locked],
    [
      "401 INVALID_CREDENTIALS credential rejection",
      401,
      "INVALID_CREDENTIALS",
      zhCN.login.errors.invalidCredentials,
    ],
    ["403 TENANT_NOT_FOUND", 403, "TENANT_NOT_FOUND", zhCN.login.errors.tenantNotFound],
    ["403 TENANT_AMBIGUOUS", 403, "TENANT_AMBIGUOUS", zhCN.login.errors.tenantAmbiguous],
    ["422 VALIDATION_ERROR", 422, "VALIDATION_ERROR", zhCN.login.errors.validation],
    ["404 capability closed", 404, "NOT_FOUND", zhCN.login.errors.capabilityClosed],
  ])("shows the mapped copy for %s", async (_case, status, code, copy) => {
    useAuthMock.mockReturnValue({
      loginWithPassword: vi.fn().mockRejectedValue(apiError(status, code)),
    });
    renderWithIntl(<LocalLoginForm />);

    fillCredentials();
    submit();

    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(copy));
  });

  it("states the 15-minute lockout window in the locked copy", () => {
    expect(zhCN.login.errors.locked).toContain("15 分钟");
  });

  it("shows a retryable copy when the backend cannot be reached", async () => {
    useAuthMock.mockReturnValue({
      loginWithPassword: vi.fn().mockRejectedValue(new Error("network down")),
    });
    renderWithIntl(<LocalLoginForm />);

    fillCredentials();
    submit();

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe(zhCN.login.errors.generic),
    );
  });
});
