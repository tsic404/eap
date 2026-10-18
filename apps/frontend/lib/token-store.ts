/**
 * Access-token store: this tab's memory is the only place the token lives.
 *
 * The backend hands the token out in the `POST /api/auth/refresh` response
 * body, so nothing here may persist it — a reload starts empty and re-enters
 * the refresh channel (C-2 / C-3). Session state shared with other tabs travels
 * over `auth-channel`, which carries no credential.
 */

let memoryToken: string | null = null;

/** Token for request authorisation; `null` until the refresh channel supplies one. */
export function getAccessToken(): string | null {
  return memoryToken;
}

/** Replace the token, or drop it (`null`) when the session cannot renew. */
export function setAccessToken(token: string | null): void {
  memoryToken = token;
}
