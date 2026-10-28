"use client";

import axios from "axios";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { API_ROUTES, AUTH_ROUTES, ROUTES } from "@/lib/api-routes";
import { publishAuthSignal, subscribeAuthSignal } from "@/lib/auth-channel";
import { isPasswordChangeRequiredError, loginWithLocalAccount } from "@/lib/auth-service";
import type { ApiEnvelope, CurrentUser, Tenant } from "@/lib/auth-types";
import { apiClient } from "@/lib/http-client";
import { refreshAccessToken } from "@/lib/refresh-channel";
import { resolveRole } from "@/lib/roles";
import { clearRoleCookie, setRoleCookie } from "@/lib/role-store";
import { getAccessToken, setAccessToken } from "@/lib/token-store";

export interface AuthContextValue {
  user: CurrentUser | null;
  tenant: Tenant | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  /** The session is usable only for the forced password change (C-113). */
  isPasswordChangeRequired: boolean;
  error: string | null;
  login: (provider: string) => void;
  loginWithPassword: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// The profile read is part of bootstrap; a request that never answers must not
// hold the page in its loading state.
const AUTH_PROFILE_TIMEOUT_MS = 8_000;

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isPasswordChangeRequired, setIsPasswordChangeRequired] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadUser = useCallback(async (): Promise<CurrentUser | null> => {
    try {
      const response = await apiClient.get<ApiEnvelope<CurrentUser>>(API_ROUTES.me, {
        timeout: AUTH_PROFILE_TIMEOUT_MS,
      });
      const currentUser = response.data?.data ?? null;
      if (currentUser) {
        setRoleCookie(resolveRole(currentUser.role));
      } else {
        clearRoleCookie();
      }
      setUser(currentUser);
      setTenant(currentUser ? { id: currentUser.tenantId } : null);
      setIsPasswordChangeRequired(false);
      setError(null);
      return currentUser;
    } catch (err) {
      // Any /api/me failure drops the confirmed user; clear the role cookie so
      // the middleware gate does not serve /admin on stale state (least
      // privilege). Only non-401 errors are surfaced — a 401 just means "not
      // authenticated", and a forced-change refusal is guidance to follow, not
      // a load failure.
      clearRoleCookie();
      setUser(null);
      setTenant(null);
      const isUnauthenticated = axios.isAxiosError(err) && err.response?.status === 401;
      const mustChangePassword = isPasswordChangeRequiredError(err);
      setIsPasswordChangeRequired(mustChangePassword);
      if (mustChangePassword || isUnauthenticated) {
        setError(null);
      } else {
        setError("无法加载用户信息，请稍后重试");
      }
      return null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (!getAccessToken()) {
          const token = await refreshAccessToken();
          // A failed refresh leaves no session; drop any stale role cookie so
          // the middleware gate falls back to least privilege.
          if (!token) {
            clearRoleCookie();
            return;
          }
        }
        if (!cancelled && getAccessToken()) {
          await loadUser();
        }
      } finally {
        // The loading state must always settle: an unresolved one is the
        // "正在完成登录…" page that never reaches either the workspace or login.
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadUser]);

  // A sign-out in another tab ends the session for the whole profile: the
  // refresh cookie the tabs share is revoked, so this tab's in-memory token is
  // dead weight and the confirmed user has to go with it.
  useEffect(
    () =>
      subscribeAuthSignal((signal) => {
        if (signal !== "sign-out") return;
        setAccessToken(null);
        clearRoleCookie();
        setUser(null);
        setTenant(null);
      }),
    [],
  );

  const login = useCallback((provider: string) => {
    window.location.assign(
      `${AUTH_ROUTES.login}?provider=${encodeURIComponent(provider)}`,
    );
  }, []);

  const loginWithPassword = useCallback(
    async (email: string, password: string) => {
      const result = await loginWithLocalAccount(email, password);
      // The login response carries the refresh cookie and no access token, so
      // the session is opened exactly like the SSO landing: refresh channel
      // first, then the profile.
      const token = await refreshAccessToken();
      if (!token) {
        throw new Error("Local login succeeded but the refresh channel returned no access token");
      }
      if (result.mustChangePassword) {
        // The profile endpoint refuses a forced-change account, so the session
        // is opened only up to the password page (C-113).
        clearRoleCookie();
        window.location.assign(ROUTES.password);
        return;
      }
      await loadUser();
      window.location.assign(ROUTES.home);
    },
    [loadUser],
  );

  const logout = useCallback(async () => {
    try {
      await apiClient.post(API_ROUTES.logout);
    } catch {
      // Local cleanup still runs even if the backend call fails.
    } finally {
      setUser(null);
      setTenant(null);
      setIsPasswordChangeRequired(false);
      setError(null);
      setAccessToken(null);
      clearRoleCookie();
      publishAuthSignal("sign-out");
      window.location.assign(ROUTES.login);
    }
  }, []);

  const refreshToken = useCallback(async () => {
    const token = await refreshAccessToken();
    if (token) {
      await loadUser();
    } else {
      clearRoleCookie();
    }
    return token;
  }, [loadUser]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      tenant,
      isLoading,
      isAuthenticated: user !== null,
      isPasswordChangeRequired,
      error,
      login,
      loginWithPassword,
      logout,
      refreshToken,
    }),
    [
      user,
      tenant,
      isLoading,
      isPasswordChangeRequired,
      error,
      login,
      loginWithPassword,
      logout,
      refreshToken,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
