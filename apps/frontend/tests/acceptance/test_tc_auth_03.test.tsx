/**
 * TC-auth-03 — a failed callback lands on the login page as a readable auth
 * failure, never as its machine code (AC-03, C-24).
 *
 * The cases drive the real login page inside the real auth provider: the page
 * boots the session exactly as a real arrival does (no credential, refresh
 * refused), so what is asserted is the page a visitor sees and where it can go
 * from there — the warning card, the retry entry, and the absence of a session.
 *
 * Evidence boundary: the credential cookie is HttpOnly and issued by the
 * backend, so "no new credential cookie" is judged by the server-side suite
 * (C-24); here the frontend's own role marker and the workspace route stand in
 * for it, and jsdom performs no navigation — "reaches the workspace" is read
 * from the router call the page makes.
 */
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import type { Mock } from "vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { replaceMock } = vi.hoisted(() => ({ replaceMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: replaceMock }),
}));

import { AuthProvider } from "@/components/auth/auth-context";
import { LoginPage } from "@/components/auth/login-page";
import { renderWithIntl } from "@/components/test-utils";
import { apiClient } from "@/lib/http-client";
import { getAccessToken, setAccessToken } from "@/lib/token-store";

export const TC_ID = "TC-auth-03";

const AUTH_FAILED_COPY = "认证失败，请重试";
const RETRY_ENTRY = "SSO 登录";
const SSO_START = "/api/auth/login?provider=oidc";

const originalLocation = window.location;

const fetchMock = vi.fn();

/** The login page's own view of the address bar; jsdom applies no navigation. */
function stubLocation(search: string): { assign: Mock } {
  const assign = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...originalLocation, assign, search, pathname: "/login" },
  });
  return { assign };
}

/** Answer the capability read point; nothing else is requested on this page. */
function installCapabilityAdapter(localLoginEnabled = false): void {
  apiClient.defaults.adapter = async (config: InternalAxiosRequestConfig) =>
    ({
      data: { code: 0, data: { localLoginEnabled }, message: "" },
      status: 200,
      statusText: "",
      headers: {},
      config,
    }) as AxiosResponse;
}

function renderLoginPage(): void {
  renderWithIntl(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>,
  );
}

beforeEach(() => {
  replaceMock.mockReset();
  fetchMock.mockReset();
  // The callback failed without minting a credential, so the refresh is refused.
  fetchMock.mockImplementation(
    async () =>
      ({
        ok: false,
        status: 401,
        json: async () => ({ error: { code: "TOKEN_EXPIRED", message: "Refresh token expired" } }),
      }) as Response,
  );
  vi.stubGlobal("fetch", fetchMock);
  setAccessToken(null);
  installCapabilityAdapter();
});

afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
});

describe("TC-auth-03 callback failure card", () => {
  it("shows the readable failure for a tampered callback state", async () => {
    stubLocation("?error=INVALID_STATE");

    renderLoginPage();

    expect(await screen.findByRole("alert")).toHaveProperty("textContent", AUTH_FAILED_COPY);
    // Not a bare error page: the login entry is still the page's own.
    expect(screen.getByRole("button", { name: RETRY_ENTRY })).toBeTruthy();
    expect(screen.getByText("使用企业账号登录 EAP")).toBeTruthy();
  });

  it("never renders the callback's machine code", async () => {
    stubLocation("?error=INVALID_STATE");

    renderLoginPage();

    await screen.findByRole("alert");
    expect(document.body.textContent ?? "").not.toContain("INVALID_STATE");
  });

  it("leaves no session behind and keeps the workspace unreachable", async () => {
    stubLocation("?error=INVALID_STATE");

    renderLoginPage();

    await screen.findByRole("alert");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    expect(getAccessToken()).toBeNull();
    expect(document.cookie).not.toContain("eap_role");
    expect(replaceMock).not.toHaveBeenCalledWith("/user");
  });

  it("re-initiates the identity provider login from the retry entry", async () => {
    const { assign } = stubLocation("?error=INVALID_STATE");

    renderLoginPage();
    fireEvent.click(await screen.findByRole("button", { name: RETRY_ENTRY }));

    expect(assign).toHaveBeenCalledWith(SSO_START);
  });
});

describe("TC-auth-03 unified callback failure copy", () => {
  it.each([
    ["ACCOUNT_EXISTS"],
    ["OIDC_USERINFO_FAILED"],
    ["TENANT_NOT_FOUND"],
    ["TENANT_AMBIGUOUS"],
    ["UNRECOGNISED_CODE"],
  ])("renders the same failure copy for %s", async (code) => {
    stubLocation(`?error=${code}`);

    renderLoginPage();

    expect(await screen.findByRole("alert")).toHaveProperty("textContent", AUTH_FAILED_COPY);
    expect(document.body.textContent ?? "").not.toContain(code);
  });
});
