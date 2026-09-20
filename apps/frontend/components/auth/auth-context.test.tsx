import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { refreshAccessTokenMock, apiPostMock, apiGetMock } = vi.hoisted(() => ({
  refreshAccessTokenMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiGetMock: vi.fn(),
}));

vi.mock("@/lib/http-client", () => ({
  apiClient: {
    get: apiGetMock,
    post: apiPostMock,
  },
  refreshAccessToken: refreshAccessTokenMock,
}));

import { AuthProvider, useAuth } from "./auth-context";
import { clearAccessToken, setAccessToken } from "@/lib/token-store";

const COOKIE = "eap_access_token";
const ROLE_COOKIE = "eap_role";

function LogoutTrigger() {
  const { logout } = useAuth();
  return (
    <button type="button" onClick={() => void logout()}>
      logout
    </button>
  );
}

const originalLocation = window.location;

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
});

describe("AuthProvider logout", () => {
  it("expires the access cookie and redirects when the backend logout fails", async () => {
    refreshAccessTokenMock.mockResolvedValue(null);
    apiGetMock.mockResolvedValue({ data: { code: 0, data: null, message: "" } });
    apiPostMock.mockRejectedValue(new Error("backend down"));

    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, assign, pathname: "/user" },
    });

    render(
      <AuthProvider>
        <LogoutTrigger />
      </AuthProvider>,
    );

    await waitFor(() => expect(refreshAccessTokenMock).toHaveBeenCalled());

    // A stale cookie left behind by a prior login must not survive logout.
    document.cookie = `${COOKIE}=stale-token; path=/`;
    document.cookie = `${ROLE_COOKIE}=agent_admin; path=/`;
    fireEvent.click(screen.getByRole("button", { name: "logout" }));

    await waitFor(() => {
      expect(document.cookie).toBe("");
      expect(assign).toHaveBeenCalledWith("/login");
    });
  });
});

describe("AuthProvider loadUser", () => {
  afterEach(() => {
    clearAccessToken();
    document.cookie = `${ROLE_COOKIE}=; Max-Age=0; path=/`;
  });

  it("persists the backend role into the eap_role cookie", async () => {
    setAccessToken("test-token");
    apiGetMock.mockResolvedValue({
      data: {
        code: 0,
        data: {
          id: "u1",
          tenantId: "t1",
          email: "alice@acme.com",
          name: "Alice",
          department: null,
          role: "agent_admin",
          avatarText: null,
        },
        message: "",
      },
    });

    render(
      <AuthProvider>
        <div />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(document.cookie).toContain(`${ROLE_COOKIE}=agent_admin`);
    });
  });

  it("clears a stale role cookie when the bootstrap refresh fails", async () => {
    refreshAccessTokenMock.mockResolvedValue(null);
    document.cookie = `${ROLE_COOKIE}=agent_admin; path=/`;

    render(
      <AuthProvider>
        <div />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(document.cookie).toBe("");
    });
  });

  it("clears the role cookie when /api/me fails with a non-401 error", async () => {
    setAccessToken("test-token");
    document.cookie = `${ROLE_COOKIE}=agent_admin; path=/`;
    apiGetMock.mockRejectedValue(new Error("network down"));

    render(
      <AuthProvider>
        <div />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(document.cookie).not.toContain(`${ROLE_COOKIE}=`);
    });
  });
});
