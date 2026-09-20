import { ROLE_COOKIE, type UserRole } from "./roles";

/**
 * The backend's access token lives 15 minutes (`ACCESS_TOKEN_TTL_SECONDS` in
 * `apps/backend/app/auth/tokens.py`). The role cookie mirrors that lifetime so
 * a stale role can never outlive the session that granted it.
 */
export const ROLE_COOKIE_MAX_AGE_SECONDS = 900;

function writeCookie(cookie: string): void {
  if (typeof document !== "undefined") {
    document.cookie = cookie;
  }
}

/**
 * Persist the authenticated role for the middleware gate and the server-side
 * `RoleProvider` (both read `eap_role` from the request cookie).
 */
export function setRoleCookie(role: UserRole): void {
  writeCookie(
    `${ROLE_COOKIE}=${role}; SameSite=Lax; Path=/; Max-Age=${ROLE_COOKIE_MAX_AGE_SECONDS}`,
  );
}

/** Drop the role cookie so a logged-out session never retains admin access. */
export function clearRoleCookie(): void {
  writeCookie(`${ROLE_COOKIE}=; Max-Age=0; Path=/`);
}
