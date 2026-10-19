import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { postMock, requestUse, responseUse, clientMock, toastMock } = vi.hoisted(() => {
  const postMock = vi.fn();
  const requestUse = vi.fn();
  const responseUse = vi.fn();
  const clientMock = Object.assign(vi.fn(), {
    interceptors: {
      request: { use: requestUse },
      response: { use: responseUse },
    },
  });
  return { postMock, requestUse, responseUse, clientMock, toastMock: vi.fn() };
});

vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => clientMock),
    post: postMock,
  },
}));

vi.mock("./toast-bus", () => ({
  toast: toastMock,
}));

import { refreshAccessToken } from "./http-client";
import { getAccessToken, setAccessToken } from "./token-store";

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
  postMock.mockReset();
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

describe("refreshAccessToken", () => {
  it("keeps the access token from the refresh body in memory only", async () => {
    postMock.mockImplementationOnce(async () => ({
      data: { code: 0, data: { accessToken: "new-token", expiresIn: 900 } },
    }));

    const token = await refreshAccessToken();

    expect(token).toBe("new-token");
    expect(getAccessToken()).toBe("new-token");
    expect(document.cookie).toBe("");
    expect(postMock).toHaveBeenCalledTimes(1);
  });

  it("single-flights concurrent refreshes in the same tab", async () => {
    const { promise, resolve } = Promise.withResolvers<unknown>();
    postMock.mockImplementationOnce(() => promise);

    const first = refreshAccessToken();
    const second = refreshAccessToken();

    resolve({ data: { code: 0, data: { accessToken: "rotated", expiresIn: 900 } } });

    const [a, b] = await Promise.all([first, second]);
    expect(postMock).toHaveBeenCalledTimes(1);
    expect(a).toBe("rotated");
    expect(b).toBe("rotated");
  });

  it("returns null and drops the token when the refresh request fails", async () => {
    setAccessToken("stale-token");
    postMock.mockRejectedValueOnce(new Error("network"));

    const token = await refreshAccessToken();

    expect(token).toBeNull();
    expect(getAccessToken()).toBeNull();
  });

  it("returns null when the refresh body carries no token", async () => {
    postMock.mockImplementationOnce(async () => ({ data: { code: 0, data: {} } }));

    const token = await refreshAccessToken();

    expect(token).toBeNull();
    expect(getAccessToken()).toBeNull();
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

    onRequest({ url: "/auth/logout", headers: { set: headersSet } });

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
    postMock.mockImplementationOnce(async () => ({
      data: { code: 0, data: { accessToken: "fresh-token", expiresIn: 900 } },
    }));

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
    postMock.mockRejectedValueOnce(new Error("refresh failed"));
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
    expect(postMock).not.toHaveBeenCalled();
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
