import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import zhCN from "../../messages/zh-CN.json";

const { postMock, setAccessTokenMock, clearRoleCookieMock } = vi.hoisted(() => ({
  postMock: vi.fn(),
  setAccessTokenMock: vi.fn(),
  clearRoleCookieMock: vi.fn(),
}));

vi.mock("@/lib/http-client", () => ({
  apiClient: { post: postMock },
}));

vi.mock("@/lib/token-store", () => ({
  setAccessToken: setAccessTokenMock,
}));

vi.mock("@/lib/role-store", () => ({
  clearRoleCookie: clearRoleCookieMock,
}));

import { renderWithIntl } from "../test-utils";
import { PasswordChangeForm } from "./password-change-form";

const CURRENT = "Local-Account-1";
const NEXT = "Rotated-Account-2";

/** One code point, two UTF-16 code units: the server counts the former. */
const EMOJI = "😀";

const originalLocation = window.location;

function stubLocation() {
  const assign = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...originalLocation, assign, pathname: "/password" },
  });
  return { assign };
}

function apiError(status: number, code: string): unknown {
  return {
    isAxiosError: true,
    response: { status, data: { error: { code } } },
    message: "Request failed",
  };
}

function fill(current: string, next: string, confirm = next) {
  fireEvent.change(screen.getByLabelText("当前口令"), { target: { value: current } });
  fireEvent.change(screen.getByLabelText("新口令"), { target: { value: next } });
  fireEvent.change(screen.getByLabelText("确认新口令"), { target: { value: confirm } });
}

function submit() {
  fireEvent.click(screen.getByRole("button", { name: "修改口令" }));
}

beforeEach(() => {
  postMock.mockReset();
  setAccessTokenMock.mockReset();
  clearRoleCookieMock.mockReset();
});

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
});

describe("PasswordChangeForm validation", () => {
  it.each([
    ["7 个字符", "a".repeat(7), zhCN.password.errors.length],
    ["129 个字符", "a".repeat(129), zhCN.password.errors.length],
    ["4 个 emoji（4 码点 / 8 码元）", EMOJI.repeat(4), zhCN.password.errors.length],
    ["与当前口令相同", CURRENT, zhCN.password.errors.unchanged],
  ])("rejects a new password with %s", (_case, next, copy) => {
    renderWithIntl(<PasswordChangeForm />);

    fill(CURRENT, next);
    submit();

    expect(screen.getByText(copy)).toBeTruthy();
    expect(postMock).not.toHaveBeenCalled();
  });

  it("rejects a confirmation that does not match the new password", () => {
    renderWithIntl(<PasswordChangeForm />);

    fill(CURRENT, NEXT, "Rotated-Account-3");
    submit();

    expect(screen.getByText(zhCN.password.errors.mismatch)).toBeTruthy();
    expect(postMock).not.toHaveBeenCalled();
  });

  it.each([
    ["8 个字符下界", "b".repeat(8)],
    ["128 个字符上界", "c".repeat(128)],
    ["65 个 emoji（65 码点 / 130 码元）", EMOJI.repeat(65)],
  ])("accepts the %s", async (_case, next) => {
    postMock.mockResolvedValue({ data: { code: 0, data: { message: "密码已修改" }, message: "" } });
    renderWithIntl(<PasswordChangeForm />);

    fill(CURRENT, next);
    submit();

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/auth/password", {
        currentPassword: CURRENT,
        newPassword: next,
      }),
    );
  });
});

describe("PasswordChangeForm submit", () => {
  it("clears the local session and returns to login after a successful change", async () => {
    const { assign } = stubLocation();
    postMock.mockResolvedValue({ data: { code: 0, data: { message: "密码已修改" }, message: "" } });
    renderWithIntl(<PasswordChangeForm />);

    fill(CURRENT, NEXT);
    submit();

    await waitFor(() => expect(setAccessTokenMock).toHaveBeenCalledWith(null));
    expect(clearRoleCookieMock).toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe(zhCN.password.success);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"), { timeout: 3000 });
  });

  it.each([
    ["401 INVALID_CREDENTIALS", 401, "INVALID_CREDENTIALS", zhCN.password.errors.invalidCurrent],
    ["422 VALIDATION_ERROR", 422, "VALIDATION_ERROR", zhCN.password.errors.validation],
    ["network failure", 0, "", zhCN.password.errors.generic],
  ])("shows the mapped copy for %s", async (_case, status, code, copy) => {
    postMock.mockRejectedValue(code ? apiError(status, code) : new Error("network down"));
    renderWithIntl(<PasswordChangeForm />);

    fill(CURRENT, NEXT);
    submit();

    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(copy));
    expect(setAccessTokenMock).not.toHaveBeenCalled();
    expect(clearRoleCookieMock).not.toHaveBeenCalled();
  });

  it("blocks a second submit while the first is in flight", async () => {
    const inFlight = Promise.withResolvers<void>();
    postMock.mockReturnValue(inFlight.promise);
    renderWithIntl(<PasswordChangeForm />);

    fill(CURRENT, NEXT);
    submit();

    await waitFor(() => {
      const submitButton = screen.getByRole("button", { name: /修改口令/ });
      expect(submitButton).toHaveProperty("disabled", true);
    });
    expect(postMock).toHaveBeenCalledTimes(1);
  });
});
