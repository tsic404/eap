import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as MemberServiceModule from "@/lib/member-service";

import { renderWithIntl } from "../test-utils";

const mocks = vi.hoisted(() => ({
  resetMemberPassword: vi.fn(),
  onReset: vi.fn(),
  onClose: vi.fn(),
}));

vi.mock("@/lib/member-service", async (importOriginal) => ({
  ...(await importOriginal<typeof MemberServiceModule>()),
  resetMemberPassword: (...args: unknown[]) => mocks.resetMemberPassword(...args),
}));

import { ResetPasswordDialog } from "./reset-password-dialog";

const MEMBER = {
  id: "u-local",
  email: "new@acme.com",
  name: "New",
  role: "employee",
  status: "active",
  department: null,
  avatarText: null,
  lastActiveAt: null,
  createdAt: "2026-10-18T00:00:00Z",
  accountSource: "local",
  mustChangePassword: false,
};

function axiosError(status: number, code: string): unknown {
  return {
    isAxiosError: true,
    response: { status, data: { error: { code, message: "failed" } } },
    message: "Request failed",
  };
}

describe("ResetPasswordDialog", () => {
  beforeEach(() => {
    mocks.resetMemberPassword.mockReset();
    mocks.onReset.mockReset();
    mocks.onClose.mockReset();
  });

  it("sends the administrator-provided password without adding a length rule of its own", async () => {
    mocks.resetMemberPassword.mockResolvedValue({ ...MEMBER, mustChangePassword: true });
    renderWithIntl(
      <ResetPasswordDialog member={MEMBER} onClose={mocks.onClose} onReset={mocks.onReset} />,
    );

    fireEvent.change(screen.getByLabelText("新口令"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "重置" }));

    await waitFor(() =>
      expect(mocks.resetMemberPassword).toHaveBeenCalledWith("u-local", "x"),
    );
    expect(mocks.onReset).toHaveBeenCalledWith(
      expect.objectContaining({ id: "u-local", mustChangePassword: true }),
    );
  });

  it("requires a new password before calling the backend", async () => {
    renderWithIntl(
      <ResetPasswordDialog member={MEMBER} onClose={mocks.onClose} onReset={mocks.onReset} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "重置" }));

    expect(await screen.findByText("请输入新口令")).toBeTruthy();
    expect(mocks.resetMemberPassword).not.toHaveBeenCalled();
  });

  it("reports a 404 as unavailable instead of naming a cause the response does not carry", async () => {
    mocks.resetMemberPassword.mockRejectedValue(axiosError(404, "NOT_FOUND"));
    renderWithIntl(
      <ResetPasswordDialog member={MEMBER} onClose={mocks.onClose} onReset={mocks.onReset} />,
    );

    fireEvent.change(screen.getByLabelText("新口令"), { target: { value: "New-Pass-1" } });
    fireEvent.click(screen.getByRole("button", { name: "重置" }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "该操作当前不可用，请刷新列表后重试",
    );
  });
});
