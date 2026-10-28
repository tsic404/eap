/**
 * The refresh channel (C-25 / C-3).
 *
 * These cases pin the channel's own behaviour: one request per tab, a retry
 * inside the concurrency window that keeps the session, and a bounded ending
 * for every other answer — an unbounded wait is what left the page on
 * "正在完成登录…" while the backend kept answering 409.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { refreshAccessToken } from "./refresh-channel";
import { getAccessToken, setAccessToken } from "./token-store";

const REFRESH_URL = "/api/auth/refresh";

const fetchMock = vi.fn();

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function tokenResponse(accessToken: string): Response {
  return jsonResponse(200, { code: 0, data: { accessToken, expiresIn: 900 }, message: "" });
}

function conflictResponse(): Response {
  return jsonResponse(409, {
    error: { code: "TOKEN_REFRESH_IN_PROGRESS", message: "Refresh in progress" },
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  setAccessToken(null);
  document.cookie = "";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("refreshAccessToken", () => {
  it("keeps the rotated access token in memory only", async () => {
    fetchMock.mockResolvedValueOnce(tokenResponse("new-token"));

    const token = await refreshAccessToken();

    expect(token).toBe("new-token");
    expect(getAccessToken()).toBe("new-token");
    expect(document.cookie).toBe("");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      REFRESH_URL,
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        // Survives a hard navigation: the browser stores the rotated cookie even
        // when the document that asked for it is already gone.
        keepalive: true,
      }),
    );
  });

  it("single-flights concurrent refreshes in the same tab", async () => {
    const { promise, resolve } = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(promise);

    const first = refreshAccessToken();
    const second = refreshAccessToken();
    resolve(tokenResponse("rotated"));

    const [a, b] = await Promise.all([first, second]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(a).toBe("rotated");
    expect(b).toBe("rotated");
  });

  it("retries inside the rotation window while another holder reports TOKEN_REFRESH_IN_PROGRESS", async () => {
    setAccessToken("stale-token");
    fetchMock
      .mockResolvedValueOnce(conflictResponse())
      .mockResolvedValueOnce(tokenResponse("successor"));

    const token = await refreshAccessToken();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(token).toBe("successor");
    expect(getAccessToken()).toBe("successor");
  });

  it("stops after a bounded number of conflicts and ends the session", async () => {
    fetchMock.mockImplementation(async () => conflictResponse());

    const token = await refreshAccessToken();

    expect(token).toBeNull();
    expect(getAccessToken()).toBeNull();
    // Four spaced retries past the first answer, then give up: the C-25 window
    // is 30 seconds wide and a longer loop would be answered with a family
    // revocation instead of another conflict.
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it("settles as no session when the refresh body carries no token", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { code: 0, data: {}, message: "" }));

    expect(await refreshAccessToken()).toBeNull();
    expect(getAccessToken()).toBeNull();
  });

  it("still refreshes when another tab holds the lock past the wait bound", async () => {
    // A holder that never releases must not strand this tab: the wait is
    // abandoned and the request runs anyway.
    const request = vi.fn((...args: [string, { signal?: AbortSignal }, unknown]) => {
      const { promise, reject } = Promise.withResolvers<string | null>();
      args[1].signal?.addEventListener("abort", () =>
        reject(new DOMException("aborted", "AbortError")),
      );
      return promise;
    });
    Object.defineProperty(navigator, "locks", { configurable: true, value: { request } });
    fetchMock.mockResolvedValueOnce(tokenResponse("from-fallback"));

    try {
      const token = await refreshAccessToken();

      expect(request).toHaveBeenCalledTimes(1);
      expect(token).toBe("from-fallback");
      expect(getAccessToken()).toBe("from-fallback");
    } finally {
      Reflect.deleteProperty(navigator, "locks");
    }
  });
});
