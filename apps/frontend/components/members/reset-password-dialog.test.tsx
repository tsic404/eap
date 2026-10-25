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

function axiosError(status: number, code: string, details?: unknown): unknown {
  return {
    isAxiosError: true,
    response: { status, data: { error: { code, message: "failed", details } } },
    message: "Request failed",
  };
}

const PASSWORD_REJECTIONS = [
  {
    password: "1234567",
    copy: "口令须为 8–128 个字符",
    msg: "Password must be between 8 and 128 characters",
  },
  {
    password: "a".repeat(129),
    copy: "口令须为 8–128 个字符",
    msg: "Password must be between 8 and 128 characters",
  },
  {
    password: "New-Pass-1",
    copy: "新口令不得与当前口令相同",
    msg: "Password is unchanged",
  },
];

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

  it.each(PASSWORD_REJECTIONS)(
    "echoes a rejected new password at the field: $copy",
    async ({ password, copy, msg }) => {
      mocks.resetMemberPassword.mockRejectedValue(
        axiosError(422, "VALIDATION_ERROR", [
          { type: "value_error", loc: ["body", "password"], msg },
        ]),
      );
      renderWithIntl(
        <ResetPasswordDialog
          member={MEMBER}
          onClose={mocks.onClose}
          onReset={mocks.onReset}
        />,
      );

      fireEvent.change(screen.getByLabelText("新口令"), {
        target: { value: password },
      });
      fireEvent.click(screen.getByRole("button", { name: "重置" }));

      expect(await screen.findByText(copy)).toBeTruthy();
      expect(screen.getByLabelText("新口令").getAttribute("aria-invalid")).toBe(
        "true",
      );
      expect(screen.queryByText("操作失败，请稍后重试")).toBeNull();
    },
  );

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
