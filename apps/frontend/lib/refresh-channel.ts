/**
 * The single refresh channel (C-25): the only way this browser obtains an
 * access token. Bootstrap, a replayed 401 and the login landing all await one
 * in-flight promise per tab, serialised across tabs by a Web Lock, so the tab
 * never races itself into `TOKEN_REFRESH_IN_PROGRESS`.
 */
import { AxiosError } from "axios";
import type { AxiosResponse } from "axios";

import { extractApiErrorCode } from "./api-error";
import { API_BASE_URL, API_ROUTES } from "./api-routes";
import { setAccessToken } from "./token-store";

const REFRESH_LOCK = "eap-auth-refresh";
const REFRESH_IN_PROGRESS_CODE = "TOKEN_REFRESH_IN_PROGRESS";

// C-25 keeps a rotated family alive for 30 seconds and answers a later replay
// with TOKEN_REUSE_DETECTED plus a whole-family revocation. One deadline bounds
// the whole attempt-and-retry sequence below that window and every attempt may
// only use what is left of it; with the lock wait the channel's worst case is
// about 8 seconds.
const REFRESH_CONFLICT_DEADLINE_MS = 6_000;
const REFRESH_CONFLICT_DELAYS_MS = [100, 300, 700, 1_500];
const REFRESH_LOCK_WAIT_MS = 2_000;

let refreshPromise: Promise<string | null> | null = null;

/** Reshape a failed refresh response so `extractApiErrorCode` can read its code. */
function refreshFailure(status: number, body: unknown): AxiosError {
  const response = {
    status,
    statusText: "",
    headers: {},
    config: {},
    data: body,
  } as AxiosResponse;
  return new AxiosError(
    `refresh rejected with ${status}`,
    undefined,
    undefined,
    undefined,
    response,
  );
}

function sleep(delayMs: number): Promise<void> {
  return new Promise<void>((resolve) => {
    setTimeout(resolve, delayMs);
  });
}

function readAccessToken(body: unknown): string | null {
  const token = (body as { data?: { accessToken?: unknown } } | null)?.data?.accessToken;
  return typeof token === "string" && token.length > 0 ? token : null;
}

/**
 * One refresh attempt, cut short by whatever the retry deadline has left.
 *
 * `keepalive` is what survives a hard navigation: the browser finishes the
 * request and stores the rotated cookie even though this document is gone, so
 * the next bootstrap no longer replays the superseded credential.
 */
async function refreshOnce(timeoutMs: number): Promise<string | null> {
  const response = await fetch(`${API_BASE_URL}${API_ROUTES.refresh}`, {
    method: "POST",
    credentials: "include",
    keepalive: true,
    cache: "no-store",
    signal: AbortSignal.timeout(Math.max(timeoutMs, 0)),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw refreshFailure(response.status, body);
  }
  return readAccessToken(body);
}

/**
 * Refresh, retrying inside C-25's concurrency window while the backend reports
 * `TOKEN_REFRESH_IN_PROGRESS`: another holder already rotated, so the retry
 * presents the successor cookie the browser just received. The deadline is
 * bounded — retrying past the window would revoke the family.
 */
async function requestRefresh(): Promise<string | null> {
  const deadline = Date.now() + REFRESH_CONFLICT_DEADLINE_MS;
  for (let attempt = 0; ; attempt += 1) {
    try {
      const token = await refreshOnce(deadline - Date.now());
      setAccessToken(token);
      return token;
    } catch (error) {
      const delayMs = REFRESH_CONFLICT_DELAYS_MS[attempt];
      const isInProgress = extractApiErrorCode(error) === REFRESH_IN_PROGRESS_CODE;
      const remainingMs = deadline - Date.now();
      if (!isInProgress || delayMs === undefined || delayMs >= remainingMs) {
        setAccessToken(null);
        return null;
      }
      await sleep(delayMs);
    }
  }
}

/**
 * Take the cross-tab lock, but never wait on it unbounded: a holder that stopped
 * answering must not leave the page in its loading state. Falling through to an
 * unlocked attempt is safe because the backend serialises rotation by row lock.
 */
async function requestRefreshGuarded(): Promise<string | null> {
  if (typeof navigator === "undefined" || !("locks" in navigator)) {
    return requestRefresh();
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REFRESH_LOCK_WAIT_MS);
  try {
    return await navigator.locks.request(
      REFRESH_LOCK,
      { signal: controller.signal },
      requestRefresh,
    );
  } catch (error) {
    if (controller.signal.aborted) {
      return requestRefresh();
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/** Refresh the access token; resolves `null` when the session cannot be renewed. */
export function refreshAccessToken(): Promise<string | null> {
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      return await requestRefreshGuarded();
    } catch {
      // The channel's contract is a settled answer, never a rejection: callers
      // hold the page's loading state on it.
      setAccessToken(null);
      return null;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}
