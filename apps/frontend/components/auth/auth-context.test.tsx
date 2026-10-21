import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { publishAuthSignal, type AuthSignal } from "@/lib/auth-channel";

const {
  refreshAccessTokenMock,
  apiPostMock,
  apiGetMock,
  loginWithLocalAccountMock,
  channelMock,
} = vi.hoisted(() => ({
  refreshAccessTokenMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiGetMock: vi.fn(),
  loginWithLocalAccountMock: vi.fn(),
  channelMock: { listener: null as null | ((signal: AuthSignal) => void) },
}));

vi.mock("@/lib/http-client", () => ({
  apiClient: {
    get: apiGetMock,
    post: apiPostMock,
  },
  refreshAccessToken: refreshAccessTokenMock,
}));

vi.mock("@/lib/auth-service", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth-service")>();
  return { ...actual, loginWithLocalAccount: loginWithLocalAccountMock };
});

vi.mock("@/lib/auth-channel", () => ({
  publishAuthSignal: vi.fn(),
  subscribeAuthSignal: (listener: (signal: AuthSignal) => void) => {
    channelMock.listener = listener;
    return () => {
      channelMock.listener = null;
    };
  },
}));

import { AuthProvider, useAuth } from "./auth-context";
import { getAccessToken, setAccessToken } from "@/lib/token-store";

const LEGACY_COOKIE = "eap_access_token";
const ROLE_COOKIE = "eap_role";
const SAMPLE_TOKEN = "header.payload.signature";

const ALICE = {
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
};

function LogoutTrigger() {
  const { logout } = useAuth();
  return (
    <button type="button" onClick={() => void logout()}>
      logout
    </button>
  );
}

function AuthProbe() {
  const { user, isAuthenticated } = useAuth();
  return <span>{isAuthenticated ? user?.email : "anonymous"}</span>;
}

const originalLocation = window.location;

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
  channelMock.listener = null;
});

describe("AuthProvider logout", () => {
  it("clears the role cookie and redirects when the backend logout fails", async () => {
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

    document.cookie = `${ROLE_COOKIE}=agent_admin; path=/`;
    fireEvent.click(screen.getByRole("button", { name: "logout" }));

    await waitFor(() => {
      expect(assign).toHaveBeenCalledWith("/login");
    });
    expect(getAccessToken()).toBeNull();
    expect(document.cookie).not.toContain(`${ROLE_COOKIE}=`);
    expect(publishAuthSignal).toHaveBeenCalledWith("sign-out");
  });
});

describe("AuthProvider session credentials", () => {
  afterEach(() => {
    setAccessToken(null);
    document.cookie = `${LEGACY_COOKIE}=; Max-Age=0; path=/`;
    document.cookie = `${ROLE_COOKIE}=; Max-Age=0; path=/`;
    localStorage.clear();
    sessionStorage.clear();
  });

  it("leaves the access token in memory only while the session is up", async () => {
    setAccessToken(SAMPLE_TOKEN);
    apiGetMock.mockResolvedValue(ALICE);

    render(
      <AuthProvider>
        <div />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(document.cookie).toContain(`${ROLE_COOKIE}=agent_admin`);
    });
    expect(document.cookie).not.toContain(LEGACY_COOKIE);
    expect(document.cookie).not.toContain(SAMPLE_TOKEN);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("drops the session when another tab signals sign-out", async () => {
    setAccessToken(SAMPLE_TOKEN);
    apiGetMock.mockResolvedValue(ALICE);

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );
    await screen.findByText("alice@acme.com");
    expect(document.cookie).toContain(`${ROLE_COOKIE}=agent_admin`);

    channelMock.listener?.("sign-out");

    await screen.findByText("anonymous");
    expect(getAccessToken()).toBeNull();
    expect(document.cookie).not.toContain(`${ROLE_COOKIE}=`);
  });
});

describe("AuthProvider loadUser", () => {
  afterEach(() => {
    setAccessToken(null);
    document.cookie = `${ROLE_COOKIE}=; Max-Age=0; path=/`;
  });

  it("persists the backend role into the eap_role cookie", async () => {
    setAccessToken("test-token");
    apiGetMock.mockResolvedValue(ALICE);

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

describe("AuthProvider local account login", () => {
  const EMAIL = "carol@acme.com";
  const PASSWORD = "Local-Account-1";

  function LocalLoginTrigger() {
    const { loginWithPassword } = useAuth();
    return (
      <button
        type="button"
        onClick={() => void loginWithPassword(EMAIL, PASSWORD).catch(() => undefined)}
      >
        local login
      </button>
    );
  }

  function stubLocation() {
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, assign, pathname: "/login" },
    });
    return { assign };
  }

  beforeEach(() => {
    refreshAccessTokenMock.mockReset();
    apiGetMock.mockReset();
    loginWithLocalAccountMock.mockReset();
    setAccessToken(null);
  });

  it("takes the access token from the refresh channel and lands the workspace", async () => {
    const { assign } = stubLocation();
    // The mount bootstrap finds no session; the refresh issued after the login
    // is the one that mints the access token.
    refreshAccessTokenMock.mockResolvedValueOnce(null).mockResolvedValue("fresh-token");
    loginWithLocalAccountMock.mockResolvedValue({ mustChangePassword: false });
    apiGetMock.mockResolvedValue({
      data: {
        code: 0,
        data: {
          id: "u1",
          tenantId: "t1",
          email: EMAIL,
          name: "Carol",
          department: null,
          role: "agent_admin",
          avatarText: null,
        },
        message: "",
      },
    });

    render(
      <AuthProvider>
        <LocalLoginTrigger />
      </AuthProvider>,
    );
    await waitFor(() => expect(refreshAccessTokenMock).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "local login" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/user"));
    expect(loginWithLocalAccountMock).toHaveBeenCalledWith(EMAIL, PASSWORD);
    // The session is loaded only after the credentials were accepted...
    expect(loginWithLocalAccountMock.mock.invocationCallOrder[0]).toBeLessThan(
      apiGetMock.mock.invocationCallOrder[0],
    );
    // ...and the token arrives through the refresh channel, never a persisted store.
    expect(refreshAccessTokenMock.mock.invocationCallOrder[1]).toBeLessThan(
      apiGetMock.mock.invocationCallOrder[0],
    );
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("establishes no session when the credentials are rejected", async () => {
    const { assign } = stubLocation();
    refreshAccessTokenMock.mockResolvedValue(null);
    loginWithLocalAccountMock.mockRejectedValue({
      isAxiosError: true,
      response: { status: 401, data: { error: { code: "INVALID_CREDENTIALS" } } },
      message: "Request failed",
    });

    render(
      <AuthProvider>
        <LocalLoginTrigger />
      </AuthProvider>,
    );
    await waitFor(() => expect(refreshAccessTokenMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "local login" }));

    await waitFor(() => expect(loginWithLocalAccountMock).toHaveBeenCalled());
    expect(assign).not.toHaveBeenCalled();
    expect(apiGetMock).not.toHaveBeenCalled();
    expect(refreshAccessTokenMock).toHaveBeenCalledTimes(1);
  });
});

describe("AuthProvider forced password change", () => {
  const EMAIL = "erin@acme.com";
  const PASSWORD = "Local-Account-1";

  function LocalLoginTrigger() {
    const { loginWithPassword } = useAuth();
    return (
      <button
        type="button"
        onClick={() => void loginWithPassword(EMAIL, PASSWORD).catch(() => undefined)}
      >
        local login
      </button>
    );
  }

  function ErrorProbe() {
    const { error, isLoading } = useAuth();
    return <p>{isLoading ? "loading" : (error ?? "none")}</p>;
  }

  function stubLocation(pathname = "/login") {
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, assign, pathname },
    });
    return { assign };
  }

  beforeEach(() => {
    refreshAccessTokenMock.mockReset();
    apiGetMock.mockReset();
    loginWithLocalAccountMock.mockReset();
    setAccessToken(null);
  });

  it("sends a forced-change account to the password page without loading the profile", async () => {
    const { assign } = stubLocation();
    refreshAccessTokenMock.mockResolvedValueOnce(null).mockResolvedValue("fresh-token");
    loginWithLocalAccountMock.mockResolvedValue({ mustChangePassword: true });

    render(
      <AuthProvider>
        <LocalLoginTrigger />
      </AuthProvider>,
    );
    await waitFor(() => expect(refreshAccessTokenMock).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "local login" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/password"));
    expect(assign).not.toHaveBeenCalledWith("/user");
    // The profile endpoint refuses a forced-change account; loading it would
    // turn the guidance into a load failure screen.
    expect(apiGetMock).not.toHaveBeenCalled();
  });

  it("does not surface the forced-change 403 as a profile load error", async () => {
    setAccessToken("test-token");
    apiGetMock.mockRejectedValue({
      isAxiosError: true,
      response: { status: 403, data: { error: { code: "PASSWORD_CHANGE_REQUIRED" } } },
      message: "Request failed",
    });

    render(
      <AuthProvider>
        <ErrorProbe />
      </AuthProvider>,
    );

    await waitFor(() => expect(apiGetMock).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("none")).toBeTruthy());
  });
});
