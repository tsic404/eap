import axios, { AxiosError } from "axios";
import type { AxiosInstance, InternalAxiosRequestConfig } from "axios";
import * as Sentry from "@sentry/nextjs";

import { API_BASE_URL, API_ROUTES, ROUTES } from "./api-routes";
import { PASSWORD_CHANGE_REQUIRED_CODE } from "./auth-types";
import { refreshAccessToken } from "./refresh-channel";
import { getAccessToken } from "./token-store";
import { toast } from "./toast-bus";

/** Shared axios instance — every API call routes through the JWT interceptors. */
export const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean };

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
        const { token, sessionEnded } = await refreshAccessToken();
        if (token) {
          config.headers.set("Authorization", `Bearer ${token}`);
          return apiClient(config);
        }
        // A conflict inside C-25's window is not a sign-out; navigating away
        // would strand a session the backend still holds.
        if (sessionEnded) redirectToLogin();
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
