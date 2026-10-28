import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { refreshMock, requestUse, responseUse, clientMock, toastMock } = vi.hoisted(() => {
  const refreshMock = vi.fn();
  const requestUse = vi.fn();
  const responseUse = vi.fn();
  const clientMock = Object.assign(vi.fn(), {
    interceptors: {
      request: { use: requestUse },
      response: { use: responseUse },
    },
  });
  return { refreshMock, requestUse, responseUse, clientMock, toastMock: vi.fn() };
});

vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => clientMock),
  },
}));

vi.mock("./refresh-channel", () => ({
  refreshAccessToken: refreshMock,
}));

vi.mock("./toast-bus", () => ({
  toast: toastMock,
}));

// Loading the module is what registers the interceptors under test.
import "./http-client";
import { setAccessToken } from "./token-store";

const LEGACY_COOKIE = "eap_access_token";

const originalLocation = window.location;

function stubLocation(pathname = "/user") {
  const assign = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...originalLocation, pathname, assign },
  });
  return { assign };
}

beforeEach(() => {
  refreshMock.mockReset();
  clientMock.mockReset();
  setAccessToken(null);
  document.cookie = `${LEGACY_COOKIE}=; path=/; Max-Age=0`;
});

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
});

describe("request interceptor", () => {
  // Logout revokes the access token by the `jti` of the bearer token it
  // receives, so the token must reach the request as a header.
  it("attaches the stored access token to the logout request", () => {
    setAccessToken("logout-token");
    const onRequest = requestUse.mock.calls[0][0];
    const headersSet = vi.fn();

    onRequest({ url: "/auth/logout", headers: { set: headersSet } });

    expect(headersSet).toHaveBeenCalledWith("Authorization", "Bearer logout-token");
  });

  it("sends no Authorization header when no token is stored", () => {
    const onRequest = requestUse.mock.calls[0][0];
    const headersSet = vi.fn();

    onRequest({ url: "/me", headers: { set: headersSet } });

    expect(headersSet).not.toHaveBeenCalled();
  });

  it("ignores an access-token cookie a page script could have written", () => {
    document.cookie = `${LEGACY_COOKIE}=forged-token; path=/`;
    const onRequest = requestUse.mock.calls[0][0];
    const headersSet = vi.fn();

    onRequest({ url: "/me", headers: { set: headersSet } });

    expect(headersSet).not.toHaveBeenCalled();
  });
});

describe("response interceptor", () => {
  it("replays a 401 request after refreshing", async () => {
    refreshMock.mockResolvedValueOnce("fresh-token");

    const onRejected = responseUse.mock.calls[0][1];
    const headersSet = vi.fn();
    const config = { url: "/me", headers: { set: headersSet } };
    clientMock.mockResolvedValueOnce({ data: { code: 0, data: {}, message: "" } });

    await onRejected({ config, response: { status: 401 } });

    expect(headersSet).toHaveBeenCalledWith("Authorization", "Bearer fresh-token");
    expect(clientMock).toHaveBeenCalledWith(config);
    expect(config).toHaveProperty("_retried", true);
    expect(document.cookie).toBe("");
  });

  it("redirects to login when the refresh fails", async () => {
    refreshMock.mockResolvedValueOnce(null);
    const { assign } = stubLocation("/user");

    const onRejected = responseUse.mock.calls[0][1];
    const config = { url: "/me", headers: { set: vi.fn() } };

    await expect(onRejected({ config, response: { status: 401 } })).rejects.toBeTruthy();
    expect(assign).toHaveBeenCalledWith("/login");
  });

  it("does not refresh auth endpoints on 401", async () => {
    const onRejected = responseUse.mock.calls[0][1];
    const config = { url: "/auth/logout", headers: { set: vi.fn() } };

    await expect(onRejected({ config, response: { status: 401 } })).rejects.toBeTruthy();
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("raises the permission toast for a 403 on a business request", async () => {
    toastMock.mockClear();
    const onRejected = responseUse.mock.calls[0][1];
    const config = { url: "/agents", headers: { set: vi.fn() } };

    await expect(onRejected({ config, response: { status: 403 } })).rejects.toBeTruthy();
    expect(toastMock).toHaveBeenCalledWith({ type: "error", title: "无权限访问" });
  });

  it("leaves a rejection from an auth endpoint to the caller's own message", async () => {
    toastMock.mockClear();
    const onRejected = responseUse.mock.calls[0][1];
    const config = { url: "/auth/login/local", headers: { set: vi.fn() } };

    await expect(onRejected({ config, response: { status: 403 } })).rejects.toBeTruthy();
    expect(toastMock).not.toHaveBeenCalled();
  });
});

describe("response interceptor forced password change", () => {
  function forcedChangeError(config: object) {
    return {
      config,
      response: { status: 403, data: { error: { code: "PASSWORD_CHANGE_REQUIRED" } } },
    };
  }

  it("guides a must-change account to the password page instead of the permission toast", async () => {
    toastMock.mockClear();
    const { assign } = stubLocation("/user");
    const onRejected = responseUse.mock.calls[0][1];
    const config = { url: "/me", headers: { set: vi.fn() } };

    await expect(onRejected(forcedChangeError(config))).rejects.toBeTruthy();

    expect(assign).toHaveBeenCalledWith("/password");
    expect(toastMock).not.toHaveBeenCalled();
  });

  it("does not navigate away when the password page itself reports the requirement", async () => {
    toastMock.mockClear();
    const { assign } = stubLocation("/password");
    const onRejected = responseUse.mock.calls[0][1];
    const config = { url: "/me", headers: { set: vi.fn() } };

    await expect(onRejected(forcedChangeError(config))).rejects.toBeTruthy();

    expect(assign).not.toHaveBeenCalled();
    expect(toastMock).not.toHaveBeenCalled();
  });

  it("refreshes and replays a 401 from the password change route", async () => {
    refreshMock.mockResolvedValueOnce("fresh-token");

    const onRejected = responseUse.mock.calls[0][1];
    const headersSet = vi.fn();
    const config = { url: "/auth/password", headers: { set: headersSet } };
    clientMock.mockResolvedValueOnce({ data: { code: 0, data: {}, message: "" } });

    await onRejected({ config, response: { status: 401 } });

    expect(headersSet).toHaveBeenCalledWith("Authorization", "Bearer fresh-token");
    expect(clientMock).toHaveBeenCalledWith(config);
  });
});
