/**
 * The single refresh channel (C-25): the only way this browser obtains an
 * access token. Bootstrap, a replayed 401 and the login landing all await one
 * in-flight promise per tab, serialised across tabs by a Web Lock, so the tab
 * never races itself into `TOKEN_REFRESH_IN_PROGRESS`. A dead document's lock
 * dies with it, so a stamp in session storage carries the same handoff across
 * the navigation that killed it.
 */
import { AxiosError, isAxiosError } from "axios";
import type { AxiosResponse } from "axios";

import { extractApiErrorCode } from "./api-error";
import { API_BASE_URL, API_ROUTES } from "./api-routes";
import { setAccessToken } from "./token-store";

const REFRESH_LOCK = "eap-auth-refresh";
const REFRESH_IN_PROGRESS_CODE = "TOKEN_REFRESH_IN_PROGRESS";

// C-25 keeps a rotated family alive for 30 seconds and answers a later replay
// with TOKEN_REUSE_DETECTED plus a whole-family revocation. One deadline bounds
// the whole attempt-and-retry sequence well below that window and every attempt
// may only use what is left of it; with the lock wait the channel's worst case
// is about 10 seconds. The retries span seconds rather than milliseconds so a
// successor cookie that lands late still converges inside the window.
const REFRESH_CONFLICT_DEADLINE_MS = 8_000;
const REFRESH_CONFLICT_DELAYS_MS = [100, 300, 700, 1_500, 3_000];
const REFRESH_LOCK_WAIT_MS = 2_000;

// Floor under the Web Lock, which dies with its document: the rotation that
// document committed may still be landing. The stamp lives in session storage
// — it survives a reload in the same tab and no other tab can see it — so the
// document that replaces it can wait the gap out and then present the
// successor instead of a credential the backend has already rotated.
const REFRESH_LEASE_KEY = "eap-auth-refresh-lease";
const REFRESH_LEASE_MS = 1_000;
const REFRESH_LEASE_RENEW_MS = 250;

const LEASE_OWNER = `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;

/** What one settled attempt tells the caller about the session. */
export interface RefreshOutcome {
  /** The access token, `null` when none could be obtained. */
  token: string | null;
  /**
   * The backend refused the credential (401, family revocation included), so
   * the session is over. A `TOKEN_REFRESH_IN_PROGRESS` window, a 5xx or a
   * request that never got an answer all leave it false: C-25 keeps the session
   * through its concurrency window, and nothing else is evidence it ended.
   */
  sessionEnded: boolean;
}

let refreshPromise: Promise<RefreshOutcome> | null = null;

interface RefreshLease {
  owner: string;
  at: number;
}

/** The tab-wide store, or `null` where the browser denies it. */
function leaseStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function readLease(): RefreshLease | null {
  const store = leaseStorage();
  if (!store) return null;
  try {
    const raw = store.getItem(REFRESH_LEASE_KEY);
    const lease: Partial<RefreshLease> | null = raw === null ? null : JSON.parse(raw);
    if (!lease || typeof lease.owner !== "string" || typeof lease.at !== "number") return null;
    return { owner: lease.owner, at: lease.at };
  } catch {
    return null;
  }
}

function writeLease(): void {
  const store = leaseStorage();
  if (!store) return;
  try {
    store.setItem(REFRESH_LEASE_KEY, JSON.stringify({ owner: LEASE_OWNER, at: Date.now() }));
  } catch {
    // A refused write only costs the next document its grace.
  }
}

/** Drop the stamp, but only while it still belongs to `owner`. */
function clearLease(owner: string): void {
  const store = leaseStorage();
  if (!store) return;
  try {
    if (readLease()?.owner === owner) store.removeItem(REFRESH_LEASE_KEY);
  } catch {
    // A refused removal is corrected by the ageing rule in `awaitPredecessor`.
  }
}

/**
 * Wait until no other document's rotation is unaccounted for. A stamp still
 * being renewed belongs to a holder the Web Lock keeps this document behind; a
 * stamp that stopped moving is a document destroyed mid-refresh, and its
 * successor is what the next attempt must present.
 */
async function awaitPredecessor(deadline: number): Promise<void> {
  for (;;) {
    const lease = readLease();
    if (lease === null || lease.owner === LEASE_OWNER) return;
    const ageMs = Date.now() - lease.at;
    if (ageMs >= REFRESH_LEASE_MS) {
      clearLease(lease.owner);
      return;
    }
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) return;
    await sleep(Math.min(REFRESH_LEASE_MS - ageMs, remainingMs));
  }
}

/** Hold the stamp for the length of one attempt so a peer can see it. */
async function withLease<T>(run: () => Promise<T>): Promise<T> {
  writeLease();
  const renewal = setInterval(writeLease, REFRESH_LEASE_RENEW_MS);
  try {
    const result = await run();
    clearLease(LEASE_OWNER);
    return result;
  } catch (error) {
    // An answer settles the rotation, even a rejection. An attempt that ends
    // without one (the document was torn down, the network dropped) may still
    // land later — the request outlives the promise — so its stamp stays for
    // the next document to wait out.
    if (extractApiErrorCode(error) !== undefined) clearLease(LEASE_OWNER);
    throw error;
  } finally {
    clearInterval(renewal);
  }
}

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
 * How a rejected attempt reads against C-25. Only the credential being refused
 * ends the session; a conflict is the concurrency window that keeps it, and a
 * rejection without an answer (a transport failure, a document torn down
 * mid-request) says nothing at all.
 */
function classifyRejection(error: unknown): { conflict: boolean; sessionEnded: boolean } {
  if (!isAxiosError(error)) return { conflict: false, sessionEnded: false };
  if (extractApiErrorCode(error) === REFRESH_IN_PROGRESS_CODE) {
    return { conflict: true, sessionEnded: false };
  }
  return { conflict: false, sessionEnded: error.response?.status === 401 };
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
async function requestRefresh(): Promise<RefreshOutcome> {
  const deadline = Date.now() + REFRESH_CONFLICT_DEADLINE_MS;
  for (let attempt = 0; ; attempt += 1) {
    try {
      await awaitPredecessor(deadline);
      const token = await withLease(() => refreshOnce(deadline - Date.now()));
      setAccessToken(token);
      return { token, sessionEnded: false };
    } catch (error) {
      const { conflict, sessionEnded } = classifyRejection(error);
      const delayMs = REFRESH_CONFLICT_DELAYS_MS[attempt];
      const remainingMs = deadline - Date.now();
      if (!conflict || delayMs === undefined || delayMs >= remainingMs) {
        setAccessToken(null);
        return { token: null, sessionEnded };
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
async function requestRefreshGuarded(): Promise<RefreshOutcome> {
  if (typeof navigator === "undefined" || !("locks" in navigator)) {
    return requestRefresh();
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REFRESH_LOCK_WAIT_MS);
  let started = false;
  try {
    return await navigator.locks.request(REFRESH_LOCK, { signal: controller.signal }, () => {
      // The bound is for the wait, not for the rotation: once granted, the
      // attempt runs under the channel's own deadline, and an abort landing
      // mid-flight must never start a second one.
      clearTimeout(timer);
      started = true;
      return requestRefresh();
    });
  } catch (error) {
    if (!started && controller.signal.aborted) {
      return requestRefresh();
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Refresh the access token. Never rejects: callers hold the page's loading
 * state on a settled answer, which says both what the token is and whether the
 * backend was reached at all.
 */
export function refreshAccessToken(): Promise<RefreshOutcome> {
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      return await requestRefreshGuarded();
    } catch {
      setAccessToken(null);
      return { token: null, sessionEnded: false };
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}
