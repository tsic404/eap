import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { postMock, responseUse, clientMock } = vi.hoisted(() => {
  const postMock = vi.fn();
  const requestUse = vi.fn();
  const responseUse = vi.fn();
  const clientMock = Object.assign(vi.fn(), {
    interceptors: {
      request: { use: requestUse },
      response: { use: responseUse },
    },
  });
  return { postMock, responseUse, clientMock };
});

vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => clientMock),
    post: postMock,
  },
}));

import { refreshAccessToken } from "./http-client";
import { clearAccessToken, readAccessTokenCookie, setAccessToken } from "./token-store";

const COOKIE = "eap_access_token";

function setCookie(value: string) {
  document.cookie = `${COOKIE}=${encodeURIComponent(value)}; path=/`;
}

function clearCookie() {
  document.cookie = `${COOKIE}=; path=/; Max-Age=0`;
}

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
  clearAccessToken();
  clearCookie();
});

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
});

describe("refreshAccessToken", () => {
  it("returns the access token from the refresh body and persists it", async () => {
    postMock.mockImplementationOnce(async () => ({
      data: { code: 0, data: { accessToken: "new-token", expiresIn: 900 } },
    }));

    const token = await refreshAccessToken();

    expect(token).toBe("new-token");
    expect(readAccessTokenCookie()).toBe("new-token");
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

  it("adopts the cookie when another tab already rotated it", async () => {
    setAccessToken("stale-token");
    setCookie("other-tab-token");

    const token = await refreshAccessToken();

    expect(token).toBe("other-tab-token");
    expect(postMock).not.toHaveBeenCalled();
  });

  it("returns null when the refresh request fails", async () => {
    postMock.mockRejectedValueOnce(new Error("network"));

    const token = await refreshAccessToken();

    expect(token).toBeNull();
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
});
