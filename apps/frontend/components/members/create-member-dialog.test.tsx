import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as MemberServiceModule from "@/lib/member-service";

import { renderWithIntl } from "../test-utils";

const mocks = vi.hoisted(() => ({
  createMember: vi.fn(),
  onCreated: vi.fn(),
  onClose: vi.fn(),
}));

vi.mock("@/lib/member-service", async (importOriginal) => ({
  ...(await importOriginal<typeof MemberServiceModule>()),
  createMember: (...args: unknown[]) => mocks.createMember(...args),
}));

vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

import { CreateMemberDialog } from "./create-member-dialog";

const MEMBER = {
  id: "u-new",
  email: "new@acme.com",
  name: "New",
  role: "employee",
  status: "active",
  department: null,
  avatarText: null,
  lastActiveAt: null,
  createdAt: "2026-10-18T00:00:00Z",
  accountSource: "local",
  mustChangePassword: true,
};

function axiosError(status: number, code: string, details?: unknown): unknown {
  return {
    isAxiosError: true,
    response: { status, data: { error: { code, message: "failed", details } } },
    message: "Request failed",
  };
}

function fillForm({
  email = "new@acme.com",
  name = "New",
  password = "Initial-Pass-1",
}: { email?: string; name?: string; password?: string } = {}) {
  fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: name } });
  fireEvent.change(screen.getByLabelText("初始口令"), { target: { value: password } });
}

function renderDialog() {
  renderWithIntl(
    <CreateMemberDialog open onClose={mocks.onClose} onCreated={mocks.onCreated} />,
  );
}

describe("CreateMemberDialog", () => {
  beforeEach(() => {
    mocks.createMember.mockReset();
    mocks.onCreated.mockReset();
    mocks.onClose.mockReset();
  });

  it("creates the local account with the fields the administrator typed", async () => {
    mocks.createMember.mockResolvedValue(MEMBER);
    renderDialog();

    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "创建" }));

    await waitFor(() =>
      expect(mocks.createMember).toHaveBeenCalledWith({
        email: "new@acme.com",
        name: "New",
        password: "Initial-Pass-1",
      }),
    );
    expect(mocks.onCreated).toHaveBeenCalledWith(MEMBER);
  });

  it("echoes a duplicate account at the email field and keeps the dialog open", async () => {
    mocks.createMember.mockRejectedValue(axiosError(409, "ACCOUNT_EXISTS"));
    renderDialog();

    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "创建" }));

    expect(await screen.findByText("该邮箱已存在账号")).toBeTruthy();
    expect(screen.getByLabelText("邮箱").getAttribute("aria-invalid")).toBe("true");
    expect(mocks.onCreated).not.toHaveBeenCalled();
  });

  it("echoes an out-of-domain email at the email field", async () => {
    mocks.createMember.mockRejectedValue(
      axiosError(422, "VALIDATION_ERROR", [
        { loc: ["body", "email"], msg: "Email must be inside the tenant domain" },
      ]),
    );
    renderDialog();

    fillForm({ email: "new@other.test" });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));

    expect(await screen.findByText("邮箱须使用本租户域名")).toBeTruthy();
  });

  it("echoes a rejected initial password at the password field", async () => {
    mocks.createMember.mockRejectedValue(
      axiosError(422, "VALIDATION_ERROR", [
        {
          type: "value_error",
          loc: ["body", "password"],
          msg: "Password must be between 8 and 128 characters",
        },
      ]),
    );
    renderDialog();

    fillForm({ password: "1234567" });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));

    expect(await screen.findByText("口令须为 8–128 个字符")).toBeTruthy();
    expect(screen.getByLabelText("初始口令").getAttribute("aria-invalid")).toBe("true");
    expect(screen.queryByText("操作失败，请稍后重试")).toBeNull();
  });

  it("reports a rejected role as a readable permission failure", async () => {
    mocks.createMember.mockRejectedValue(axiosError(403, "FORBIDDEN"));
    renderDialog();

    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "创建" }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "无权限执行此操作",
    );
  });

  it("reports a 404 as unavailable instead of naming a cause the response does not carry", async () => {
    mocks.createMember.mockRejectedValue(axiosError(404, "NOT_FOUND"));
    renderDialog();

    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "创建" }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "该操作当前不可用，请刷新列表后重试",
    );
  });
});
