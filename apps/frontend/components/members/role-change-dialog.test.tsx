import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as MemberServiceModule from "@/lib/member-service";

import { renderWithIntl } from "../test-utils";

const mocks = vi.hoisted(() => ({
  setMemberRole: vi.fn(),
  onChanged: vi.fn(),
  onClose: vi.fn(),
}));

vi.mock("@/lib/member-service", async (importOriginal) => ({
  ...(await importOriginal<typeof MemberServiceModule>()),
  setMemberRole: (...args: unknown[]) => mocks.setMemberRole(...args),
}));

import { RoleChangeDialog } from "./role-change-dialog";

const MEMBER = {
  id: "u-sso",
  email: "alice@acme.com",
  name: "Alice",
  role: "agent_admin",
  status: "active",
  department: null,
  avatarText: null,
  lastActiveAt: null,
  createdAt: "2026-10-17T00:00:00Z",
  accountSource: "sso",
  mustChangePassword: false,
};

function axiosError(status: number, code: string): unknown {
  return {
    isAxiosError: true,
    response: { status, data: { error: { code, message: "failed" } } },
    message: "Request failed",
  };
}

function renderDialog() {
  return renderWithIntl(
    <RoleChangeDialog member={MEMBER} onClose={mocks.onClose} onChanged={mocks.onChanged} />,
  );
}

function chooseRole(role: string) {
  fireEvent.change(screen.getByLabelText("角色"), { target: { value: role } });
}

describe("RoleChangeDialog", () => {
  beforeEach(() => {
    mocks.setMemberRole.mockReset();
    mocks.onChanged.mockReset();
    mocks.onClose.mockReset();
  });

  it("starts on the member's current role and sends the chosen one", async () => {
    mocks.setMemberRole.mockResolvedValue({ ...MEMBER, role: "employee" });
    renderDialog();

    expect((screen.getByLabelText("角色") as HTMLSelectElement).value).toBe("agent_admin");
    chooseRole("employee");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(mocks.setMemberRole).toHaveBeenCalledWith("u-sso", "employee"),
    );
    await waitFor(() =>
      expect(mocks.onChanged).toHaveBeenCalledWith({ ...MEMBER, role: "employee" }),
    );
  });

  it("blocks saving until the selected role differs from the current one", () => {
    renderDialog();

    expect(screen.getByRole("button", { name: "保存" })).toHaveProperty("disabled", true);
    chooseRole("auditor");
    expect(screen.getByRole("button", { name: "保存" })).toHaveProperty("disabled", false);
  });

  it("keeps the dialog open and names the last-administrator refusal on a demotion", async () => {
    mocks.setMemberRole.mockRejectedValue(axiosError(409, "LAST_ADMIN"));
    renderDialog();

    chooseRole("employee");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "不能降级最后一个平台管理员",
    );
    expect(mocks.onChanged).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("renders a refused permission as a readable failure", async () => {
    mocks.setMemberRole.mockRejectedValue(axiosError(403, "FORBIDDEN"));
    renderDialog();

    chooseRole("employee");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "无权限执行此操作",
    );
  });

  it("reports a vanished member as unavailable instead of naming a cause", async () => {
    mocks.setMemberRole.mockRejectedValue(axiosError(404, "NOT_FOUND"));
    renderDialog();

    chooseRole("employee");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "该操作当前不可用，请刷新列表后重试",
    );
  });

  it("reports an out-of-set role as a validation failure", async () => {
    mocks.setMemberRole.mockRejectedValue(axiosError(422, "VALIDATION_ERROR"));
    renderDialog();

    chooseRole("employee");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "角色取值不合法，请刷新后重试",
    );
  });
});
