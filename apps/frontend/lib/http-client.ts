import axios, { AxiosError } from "axios";
import type { AxiosInstance, InternalAxiosRequestConfig } from "axios";
import * as Sentry from "@sentry/nextjs";

import { API_ROUTES, ROUTES } from "./api-routes";
import { PASSWORD_CHANGE_REQUIRED_CODE } from "./auth-types";
import { getAccessToken, setAccessToken } from "./token-store";
import { toast } from "./toast-bus";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

/** Shared axios instance — every API call routes through the JWT interceptors. */
export const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

// Single-flight within this tab: concurrent 401s await the same refresh promise.
let refreshPromise: Promise<string | null> | null = null;

// Web Locks serialises the refresh across tabs so only one tab calls the
// backend at a time; the next one then refreshes against the rotated cookie.
const REFRESH_LOCK = "eap-auth-refresh";

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean };

async function requestRefresh(): Promise<string | null> {
  try {
    // Raw axios (not apiClient) so the response interceptor cannot re-enter.
    // The backend returns the rotated access token in the response body
    // (`{ code, data: { accessToken, expiresIn } }`); the HttpOnly refresh
    // cookie is rotated separately and is invisible to JavaScript.
    const response = await axios.post(`${API_BASE_URL}${API_ROUTES.refresh}`, null, {
      withCredentials: true,
    });
    const fresh = response.data?.data?.accessToken;
    if (typeof fresh !== "string" || fresh.length === 0) {
      setAccessToken(null);
      return null;
    }
    setAccessToken(fresh);
    return fresh;
  } catch {
    setAccessToken(null);
    return null;
  }
}

export function refreshAccessToken(): Promise<string | null> {
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      if (typeof navigator !== "undefined" && "locks" in navigator) {
        return await navigator.locks.request(REFRESH_LOCK, requestRefresh);
      }
      return await requestRefresh();
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

function redirectToLogin(): void {
  if (typeof window !== "undefined" && window.location.pathname !== ROUTES.login) {
    window.location.assign(ROUTES.login);
  }
}

/**
 * Guide an account that must change its password to the password page (C-15).
 * The page itself is exempt so a repeated gate answer cannot loop.
 */
function redirectToPasswordChange(): void {
  if (typeof window !== "undefined" && window.location.pathname !== ROUTES.password) {
    window.location.assign(ROUTES.password);
  }
}

apiClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined;
    const status = error.response?.status;
    // Auth endpoints judge the call itself: their rejections carry their own
    // meaning (e.g. a tenant mismatch on login) and are rendered by the caller,
    // so neither a refresh replay nor the generic permission toast applies.
    // The password change is the exception: it is an authenticated business
    // call whose 401 most often means an expired access token, and replaying it
    // after a refresh keeps a stale token from reading as a wrong password.
    const requestUrl = config?.url ?? "";
    const isAuthRequest =
      requestUrl.startsWith("/auth/") && requestUrl !== API_ROUTES.changePassword;

    if (status === 401 && config && !config._retried) {
      // Never attempt to refresh the auth endpoints themselves — that would loop.
      if (!isAuthRequest) {
        config._retried = true;
        const token = await refreshAccessToken();
        if (token) {
          config.headers.set("Authorization", `Bearer ${token}`);
          return apiClient(config);
        }
        redirectToLogin();
      }
    }
    if (status === 403 && !isAuthRequest) {
      const errorCode = (error.response?.data as { error?: { code?: string } } | undefined)?.error
        ?.code;
      if (errorCode === PASSWORD_CHANGE_REQUIRED_CODE) {
        redirectToPasswordChange();
      } else {
        toast({ type: "error", title: "无权限访问" });
      }
    }
    if (status !== undefined && status >= 500) {
      Sentry.captureException(error);
    }
    return Promise.reject(error);
  },
);
