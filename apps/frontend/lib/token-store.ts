/**
 * Access-token store.
 *
 * The backend delivers the access token in the `POST /api/auth/refresh` response
 * body (`data.accessToken`); `http-client.ts` persists it here. It is mirrored
 * into a JS-readable cookie so the request interceptor can attach it as a
 * bearer token without a round-trip to memory, and so a rotation performed by
 * another tab is visible to this one.
 *
 * The cookie (not memory) is the cross-tab source of truth. The refresh guard
 * in `http-client.ts` compares the raw cookie against the in-memory token to
 * detect a rotation performed by another tab while this one waited on the lock.
 */

const ACCESS_TOKEN_COOKIE = "eap_access_token";

let memoryToken: string | null = null;

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  const entry = document.cookie
    .split("; ")
    .find((part) => part.startsWith(prefix));
  if (!entry) return null;
  let value: string;
  try {
    value = decodeURIComponent(entry.slice(prefix.length));
  } catch (error) {
    // A malformed (user/proxy-tampered) cookie is treated as absent rather
    // than crashing auth bootstrap and every request interception.
    if (error instanceof URIError) return null;
    throw error;
  }
  return value.length > 0 ? value : null;
}

/** Current token for request authorisation (memory, falling back to cookie). */
export function getAccessToken(): string | null {
  if (memoryToken !== null) return memoryToken;
  memoryToken = readCookie(ACCESS_TOKEN_COOKIE);
  return memoryToken;
}

/** Raw cookie value, bypassing the memory cache — used for cross-tab rotation. */
export function readAccessTokenCookie(): string | null {
  return readCookie(ACCESS_TOKEN_COOKIE);
}

export function setAccessToken(token: string | null): void {
  memoryToken = token;
  if (typeof document !== "undefined") {
    document.cookie = token
      ? `${ACCESS_TOKEN_COOKIE}=${encodeURIComponent(token)}; path=/`
      : `${ACCESS_TOKEN_COOKIE}=; Max-Age=0; path=/`;
  }
}

/**
 * Clears the in-memory token and expires the browser cookie client-side, so a
 * locally initiated logout holds even when the backend logout request fails.
 */
export function clearAccessToken(): void {
  memoryToken = null;
  if (typeof document !== "undefined") {
    document.cookie = `${ACCESS_TOKEN_COOKIE}=; Max-Age=0; path=/`;
  }
}
