import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as MemberServiceModule from "@/lib/member-service";

import { RoleProvider } from "@/components/auth/role-context";
import { renderWithIntl } from "../test-utils";

const mocks = vi.hoisted(() => ({
  useMembers: vi.fn(),
  mutate: vi.fn(),
  fetchAuthCapabilities: vi.fn(),
  setMemberStatus: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("@/lib/use-members", () => ({
  useMembers: () => mocks.useMembers(),
}));

vi.mock("@/lib/auth-service", () => ({
  fetchAuthCapabilities: () => mocks.fetchAuthCapabilities(),
}));

vi.mock("@/lib/member-service", async (importOriginal) => ({
  ...(await importOriginal<typeof MemberServiceModule>()),
  setMemberStatus: (...args: unknown[]) => mocks.setMemberStatus(...args),
}));

vi.mock("@/components/ui/toast", () => ({
  useToast: () => ({ toast: mocks.toast }),
}));

import { MemberListPage } from "./member-list-page";

const LOCAL_MEMBER = {
  id: "u-local",
  email: "new-member@acme.com",
  name: "New Member",
  role: "employee",
  status: "active",
  department: null,
  avatarText: null,
  lastActiveAt: null,
  createdAt: "2026-10-18T00:00:00Z",
  accountSource: "local",
  mustChangePassword: true,
};

const SSO_MEMBER = {
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

function axiosError(status: number, code: string, details?: unknown): unknown {
  return {
    isAxiosError: true,
    response: { status, data: { error: { code, message: "failed", details } } },
    message: "Request failed",
  };
}

function renderPage(role: "platform_admin" | "agent_admin") {
  return renderWithIntl(
    <RoleProvider role={role}>
      <MemberListPage />
    </RoleProvider>,
  );
}

describe("MemberListPage roster", () => {
  beforeEach(() => {
    mocks.useMembers.mockClear();
    mocks.useMembers.mockReturnValue({
      data: { items: [LOCAL_MEMBER, SSO_MEMBER], total: 2 },
      error: undefined,
      isLoading: false,
      mutate: mocks.mutate,
    });
    mocks.fetchAuthCapabilities.mockResolvedValue({ localLoginEnabled: true });
    mocks.setMemberStatus.mockReset();
    mocks.toast.mockReset();
    mocks.mutate.mockReset();
  });

  it("shows each member's account source and forced-change state", async () => {
    renderPage("platform_admin");

    expect(await screen.findByText("new-member@acme.com")).toBeTruthy();
    const localRow = screen.getByRole("row", { name: /new-member@acme\.com/ });
    expect(within(localRow).getByText("本地")).toBeTruthy();
    expect(within(localRow).getByText("须改密")).toBeTruthy();

    const ssoRow = screen.getByRole("row", { name: /alice@acme\.com/ });
    expect(within(ssoRow).getByText("SSO")).toBeTruthy();
    expect(within(ssoRow).getByText("无需改密")).toBeTruthy();
  });

  it("offers local account creation and password reset while the capability is open", async () => {
    renderPage("platform_admin");

    expect(await screen.findByRole("button", { name: "新建本地账号" })).toBeTruthy();
    const localRow = screen.getByRole("row", { name: /new-member@acme\.com/ });
    expect(within(localRow).getByRole("button", { name: "重置口令" })).toBeTruthy();
  });

  it("hides creation and password reset while the capability is closed, keeping the roster and disable entry", async () => {
    mocks.fetchAuthCapabilities.mockResolvedValue({ localLoginEnabled: false });
    renderPage("platform_admin");

    // Roster and the status switch are unaffected by the closed capability (C-110).
    expect(await screen.findByText("new-member@acme.com")).toBeTruthy();
    const localRow = screen.getByRole("row", { name: /new-member@acme\.com/ });
    expect(within(localRow).getByRole("button", { name: "停用" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "新建本地账号" })).toBeNull();
    expect(within(localRow).queryByRole("button", { name: "重置口令" })).toBeNull();
  });

  it("keeps the create entry out of the empty-roster prompt while the capability is closed", async () => {
    mocks.fetchAuthCapabilities.mockResolvedValue({ localLoginEnabled: false });
    mocks.useMembers.mockReturnValue({
      data: { items: [], total: 0 },
      error: undefined,
      isLoading: false,
      mutate: mocks.mutate,
    });
    renderPage("platform_admin");

    expect(await screen.findByText("暂无成员")).toBeTruthy();
    expect(screen.getByText("调整筛选条件")).toBeTruthy();
    expect(screen.queryByText("调整筛选条件，或创建本地账号")).toBeNull();
  });

  it("offers creation from the empty-roster prompt while the capability is open", async () => {
    mocks.useMembers.mockReturnValue({
      data: { items: [], total: 0 },
      error: undefined,
      isLoading: false,
      mutate: mocks.mutate,
    });
    renderPage("platform_admin");

    expect(await screen.findByText("暂无成员")).toBeTruthy();
    expect(screen.getByText("调整筛选条件，或创建本地账号")).toBeTruthy();
  });

  it("serves the permission notice to a non-platform administrator without fetching the roster", () => {
    renderPage("agent_admin");

    expect(screen.getByRole("heading", { name: "无管理权限" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "新建本地账号" })).toBeNull();
    expect(mocks.useMembers).not.toHaveBeenCalled();
  });

  it("disables a member and refreshes the roster", async () => {
    mocks.setMemberStatus.mockResolvedValue({ ...LOCAL_MEMBER, status: "disabled" });
    renderPage("platform_admin");

    await screen.findByText("new-member@acme.com");
    const localRow = screen.getByRole("row", { name: /new-member@acme\.com/ });
    fireEvent.click(within(localRow).getByRole("button", { name: "停用" }));

    await waitFor(() => expect(mocks.setMemberStatus).toHaveBeenCalledWith("u-local", "disabled"));
    await waitFor(() => expect(mocks.mutate).toHaveBeenCalled());
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ type: "success", title: "账号已停用" }),
    );
  });

  it("reports the last-administrator refusal instead of pretending the switch landed", async () => {
    mocks.setMemberStatus.mockRejectedValue(axiosError(409, "LAST_ADMIN"));
    renderPage("platform_admin");

    await screen.findByText("new-member@acme.com");
    const localRow = screen.getByRole("row", { name: /new-member@acme\.com/ });
    fireEvent.click(within(localRow).getByRole("button", { name: "停用" }));

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({ type: "error", title: "不能停用最后一个平台管理员" }),
      ),
    );
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  it("opens the password reset dialog for a local account", async () => {
    renderPage("platform_admin");

    await screen.findByText("new-member@acme.com");
    const localRow = screen.getByRole("row", { name: /new-member@acme\.com/ });
    fireEvent.click(within(localRow).getByRole("button", { name: "重置口令" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("新口令")).toBeTruthy();
  });
});
