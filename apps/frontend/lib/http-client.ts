import axios, { AxiosError } from "axios";
import type { AxiosInstance, InternalAxiosRequestConfig } from "axios";

import { API_ROUTES, ROUTES } from "./api-routes";
import {
  getAccessToken,
  readAccessTokenCookie,
  setAccessToken,
} from "./token-store";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

/** Shared axios instance — every API call routes through the JWT interceptors. */
export const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

// Single-flight within this tab: concurrent 401s await the same refresh promise.
let refreshPromise: Promise<string | null> | null = null;

// Web Locks serialises the refresh across tabs so only one tab calls the
// backend; the others re-read the rotated cookie once the lock is released.
const REFRESH_LOCK = "eap-auth-refresh";

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean };

async function requestRefresh(): Promise<string | null> {
  // A tab that waited for the lock sees a cookie that no longer matches its
  // stale in-memory token when another tab already rotated it — adopt it
  // instead of issuing a second refresh request.
  if (readAccessTokenCookie() !== getAccessToken()) {
    const adopted = readAccessTokenCookie();
    setAccessToken(adopted);
    return adopted;
  }

  try {
    // Raw axios (not apiClient) so the response interceptor cannot re-enter.
    await axios.post(`${API_BASE_URL}${API_ROUTES.refresh}`, null, {
      withCredentials: true,
    });
  } catch {
    setAccessToken(null);
    return null;
  }

  const fresh = readAccessTokenCookie();
  setAccessToken(fresh);
  return fresh;
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

    if (status === 401 && config && !config._retried) {
      // Never attempt to refresh the auth endpoints themselves — that would loop.
      const isAuthRequest = (config.url ?? "").startsWith("/auth/");
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
    return Promise.reject(error);
  },
);
