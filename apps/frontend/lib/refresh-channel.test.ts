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
// The stamp a document leaves behind while its rotation is in flight; the next
// document reads it out of the tab-wide storage the browser shares across
// navigations (the same place the channel writes it).
const LEASE_KEY = "eap-auth-refresh-lease";

const fetchMock = vi.fn();

/** Resolve one refresh and hand back the token alone, the shape most cases judge. */
async function settleToken(): Promise<string | null> {
  return (await refreshAccessToken()).token;
}

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
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("refreshAccessToken", () => {
  it("keeps the rotated access token in memory only", async () => {
    fetchMock.mockResolvedValueOnce(tokenResponse("new-token"));

    const { token } = await refreshAccessToken();

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
    expect(a.token).toBe("rotated");
    expect(b.token).toBe("rotated");
  });

  it("retries inside the rotation window while another holder reports TOKEN_REFRESH_IN_PROGRESS", async () => {
    setAccessToken("stale-token");
    fetchMock
      .mockResolvedValueOnce(conflictResponse())
      .mockResolvedValueOnce(tokenResponse("successor"));

    const { token } = await refreshAccessToken();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(token).toBe("successor");
    expect(getAccessToken()).toBe("successor");
  });

  it(
    "stops after a bounded number of conflicts without ending the session",
    async () => {
      fetchMock.mockImplementation(async () => conflictResponse());

      const outcome = await refreshAccessToken();

      // Five spaced retries past the first answer, then give up: the C-25
      // window is 30 seconds wide and a longer loop would be answered with a
      // family revocation instead of another conflict. Giving up is not a
      // sign-out: the session is untouched and the next document tries again.
      expect(outcome).toEqual({ token: null, sessionEnded: false });
      expect(getAccessToken()).toBeNull();
      expect(fetchMock).toHaveBeenCalledTimes(6);
    },
    15_000,
  );

  it("reports the session as ended only when the backend refuses the credential", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(401, {
        error: { code: "TOKEN_REUSE_DETECTED", message: "Refresh token reuse" },
      }),
    );

    expect(await refreshAccessToken()).toEqual({ token: null, sessionEnded: true });
  });

  it("settles as no session when the refresh body carries no token", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { code: 0, data: {}, message: "" }));

    expect(await settleToken()).toBeNull();
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
      const { token } = await refreshAccessToken();

      expect(request).toHaveBeenCalledTimes(1);
      expect(token).toBe("from-fallback");
      expect(getAccessToken()).toBe("from-fallback");
    } finally {
      Reflect.deleteProperty(navigator, "locks");
    }
  });

  it("does not abort the lock wait once the rotation is running", async () => {
    // The wait bound is for a peer that never answers; expiring it mid-rotation
    // would abandon a live attempt and start a second one on the same credential.
    const request = vi.fn(
      async (
        _name: string,
        options: { signal?: AbortSignal },
        run: () => Promise<string | null>,
      ) => {
        const rotation = run();
        await new Promise((resolve) => setTimeout(resolve, 2_500));
        const aborted = options.signal?.aborted === true;
        const token = await rotation;
        return aborted ? `${token}-aborted` : token;
      },
    );
    Object.defineProperty(navigator, "locks", { configurable: true, value: { request } });
    fetchMock.mockResolvedValueOnce(tokenResponse("held-token"));
    try {
      expect(await settleToken()).toBe("held-token");
      expect(fetchMock).toHaveBeenCalledTimes(1);
    } finally {
      Reflect.deleteProperty(navigator, "locks");
    }
  });
});

describe("refreshAccessToken across documents", () => {
  it("waits out a predecessor document's unaccounted rotation before presenting the credential", async () => {
    // The document that issued the rotation is gone: a reload or a navigation
    // destroyed it while its refresh was in flight, and the successor cookie
    // may still be landing. Presenting the credential now is exactly what the
    // backend answers with TOKEN_REFRESH_IN_PROGRESS.
    sessionStorage.setItem(LEASE_KEY, JSON.stringify({ owner: "predecessor", at: Date.now() - 50 }));
    fetchMock.mockResolvedValueOnce(tokenResponse("successor"));

    const refresh = refreshAccessToken();
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(fetchMock).not.toHaveBeenCalled();

    expect((await refresh).token).toBe("successor");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // The stamp is settled with the request, so the next document starts clean.
    expect(sessionStorage.getItem(LEASE_KEY)).toBeNull();
  });

  it("refreshes at once once the predecessor's stamp is older than the grace", async () => {
    sessionStorage.setItem(LEASE_KEY, JSON.stringify({ owner: "predecessor", at: Date.now() - 5_000 }));
    fetchMock.mockResolvedValueOnce(tokenResponse("fresh"));

    const startedAt = Date.now();
    expect(await settleToken()).toBe("fresh");
    expect(Date.now() - startedAt).toBeLessThan(800);
    expect(sessionStorage.getItem(LEASE_KEY)).toBeNull();
  });

  it(
    "leaves no stamp behind when the refresh ends without a token",
    async () => {
      fetchMock.mockImplementation(async () => conflictResponse());

      expect(await settleToken()).toBeNull();
      expect(sessionStorage.getItem(LEASE_KEY)).toBeNull();
    },
    15_000,
  );

  it("holds the next document back when an attempt ended without an answer", async () => {
    // A document torn down mid-request loses the promise, not the request: the
    // rotation it may still commit is unaccounted for, so the next document
    // must wait the gap out before presenting the credential.
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const outcome = await refreshAccessToken();
    expect(outcome).toEqual({ token: null, sessionEnded: false });

    vi.resetModules();
    const successor = await import("./refresh-channel");
    fetchMock.mockResolvedValueOnce(tokenResponse("successor"));
    const refresh = successor.refreshAccessToken();
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    expect((await refresh).token).toBe("successor");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("refreshes anyway when the browser refuses storage", async () => {
    const getItem = vi.spyOn(sessionStorage, "getItem").mockImplementation(() => {
      throw new Error("storage denied");
    });
    const setItem = vi.spyOn(sessionStorage, "setItem").mockImplementation(() => {
      throw new Error("storage denied");
    });
    try {
      fetchMock.mockResolvedValueOnce(tokenResponse("unpersisted"));

      expect(await settleToken()).toBe("unpersisted");
      expect(getAccessToken()).toBe("unpersisted");
    } finally {
      getItem.mockRestore();
      setItem.mockRestore();
    }
  });
});

describe("refreshAccessToken peers", () => {
  it("keeps a peer document out while its own rotation is still in flight", async () => {
    // The Web Lock covers live tabs, but a document that denies it — or one
    // whose lock was just released by a navigation — is kept out by the stamp
    // alone. Presenting the credential while a peer is rotating is what the
    // backend answers with TOKEN_REFRESH_IN_PROGRESS.
    const { promise: held, resolve: resolveHeld } = Promise.withResolvers<Response>();
    fetchMock.mockReturnValueOnce(held).mockResolvedValueOnce(tokenResponse("peer-token"));

    const holder = refreshAccessToken();
    await new Promise((resolve) => setTimeout(resolve, 100));

    vi.resetModules();
    const peer = await import("./refresh-channel");
    const peerRefresh = peer.refreshAccessToken();
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolveHeld(tokenResponse("holder-token"));
    expect((await holder).token).toBe("holder-token");
    expect((await peerRefresh).token).toBe("peer-token");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
