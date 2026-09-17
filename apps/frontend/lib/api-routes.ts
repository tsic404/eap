/**
 * Centralised API route constants.
 *
 * Browser navigations (SSO login/callback) use origin-relative `/api/*` paths
 * that nginx proxies to the FastAPI backend. axios calls use routes relative
 * to `NEXT_PUBLIC_API_BASE_URL` (see `lib/http-client.ts`).
 */

/** Auth endpoints reached by full browser navigation. */
export const AUTH_ROUTES = {
  login: "/api/auth/login",
  callback: "/api/auth/callback",
} as const;

/** axios API routes, relative to `NEXT_PUBLIC_API_BASE_URL`. */
export const API_ROUTES = {
  refresh: "/auth/refresh",
  logout: "/auth/logout",
  me: "/me",
} as const;

/** Frontend page routes. */
export const ROUTES = {
  login: "/login",
  home: "/user",
} as const;

/** Identity provider used by the single SSO login button. */
export const DEFAULT_SSO_PROVIDER = "oidc";
