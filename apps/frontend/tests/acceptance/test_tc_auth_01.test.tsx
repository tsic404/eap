/**
 * TC-auth-01 — a signed-in visitor's top bar shows the account avatar and name
 * as visible text (AC-01). The header runs inside the real auth and role
 * providers, booted by the same refresh-then-profile sequence a landed login
 * performs, so the name asserted is the profile's, not a prop's.
 * Evidence boundary: the identity chain and the HttpOnly credential belong to
 * environment B and the backend suite; workspace page content is not here.
 */
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/user",
}));

import { AuthProvider } from "@/components/auth/auth-context";
import { RoleProvider } from "@/components/auth/role-context";
import { Header } from "@/components/layout/header";
import { renderWithIntl } from "@/components/test-utils";
import { apiClient } from "@/lib/http-client";
import { setAccessToken } from "@/lib/token-store";

export const TC_ID = "TC-auth-01";

interface Profile {
  name: string;
  avatarText: string;
}

const LIN = { name: "林晓", avatarText: "林" };
const ZHAO = { name: "赵磊", avatarText: "赵" };

const fetchMock = vi.fn();

function installAdapter({ name, avatarText }: Profile): void {
  apiClient.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
    const path = `${config.baseURL ?? ""}${config.url ?? ""}`;
    const body =
      path === "/api/me"
        ? {
            code: 0,
            data: {
              id: "u-4584",
              tenantId: "t1",
              email: "member@acme.com",
              name,
              department: "研发部",
              role: "employee",
              avatarText,
            },
            message: "",
          }
        : { code: 0, data: {}, message: "" };
    return {
      data: body,
      status: 200,
      statusText: "",
      headers: {},
      config,
    } as AxiosResponse;
  };
}

/** The top bar as a landed first login reaches it: provider first, then header. */
function renderTopBar(): void {
  renderWithIntl(
    <AuthProvider>
      <RoleProvider role="employee">
        <Header />
      </RoleProvider>
    </AuthProvider>,
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  // The landing carries a refresh credential and no page-side access token.
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ code: 0, data: { accessToken: "landing-token", expiresIn: 900 }, message: "" }),
  } as Response);
  vi.stubGlobal("fetch", fetchMock);
  setAccessToken(null);
  installAdapter(LIN);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TC-auth-01 顶部栏显示头像与姓名", () => {
  it("renders the account name as visible text in the top bar", async () => {
    renderTopBar();

    const banner = screen.getByRole("banner");
    const nameNode = await within(banner).findByText(LIN.name);

    expect(nameNode.closest('[aria-hidden="true"]')).toBeNull();
    expect(nameNode.textContent).toBe(LIN.name);
  });

  it("renders the avatar beside the name", async () => {
    renderTopBar();

    const banner = screen.getByRole("banner");
    await within(banner).findByText(LIN.name);
    expect(within(banner).getByText(LIN.avatarText)).toBeTruthy();
  });

  it.each([LIN, ZHAO])("shows $name from the profile the session resolves", async (profile) => {
    installAdapter(profile);
    renderTopBar();

    const banner = screen.getByRole("banner");
    expect(await within(banner).findByText(profile.name)).toBeTruthy();
  });
});
