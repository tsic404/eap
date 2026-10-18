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
import { loginWithLocalAccount } from "@/lib/auth-service";
import type { ApiEnvelope, CurrentUser, Tenant } from "@/lib/auth-types";
import { apiClient, refreshAccessToken } from "@/lib/http-client";
import { resolveRole } from "@/lib/roles";
import { clearRoleCookie, setRoleCookie } from "@/lib/role-store";
import { getAccessToken, setAccessToken } from "@/lib/token-store";

export interface AuthContextValue {
  user: CurrentUser | null;
  tenant: Tenant | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  error: string | null;
  login: (provider: string) => void;
  loginWithPassword: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadUser = useCallback(async (): Promise<CurrentUser | null> => {
    try {
      const response = await apiClient.get<ApiEnvelope<CurrentUser>>(API_ROUTES.me);
      const currentUser = response.data?.data ?? null;
      if (currentUser) {
        setRoleCookie(resolveRole(currentUser.role));
      } else {
        clearRoleCookie();
      }
      setUser(currentUser);
      setTenant(currentUser ? { id: currentUser.tenantId } : null);
      setError(null);
      return currentUser;
    } catch (err) {
      // Any /api/me failure drops the confirmed user; clear the role cookie so
      // the middleware gate does not serve /admin on stale state (least
      // privilege). Only non-401 errors are surfaced — a 401 just means "not
      // authenticated".
      clearRoleCookie();
      if (!(axios.isAxiosError(err) && err.response?.status === 401)) {
        setError("无法加载用户信息，请稍后重试");
      }
      setUser(null);
      setTenant(null);
      return null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!getAccessToken()) {
        const token = await refreshAccessToken();
        // A failed refresh leaves no session; drop any stale role cookie so
        // the middleware gate falls back to least privilege.
        if (!token) {
          clearRoleCookie();
        }
      }
      if (!cancelled && getAccessToken()) {
        await loadUser();
      }
      if (!cancelled) setIsLoading(false);
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
      await loginWithLocalAccount(email, password);
      // The login response carries the refresh cookie and no access token, so
      // the session is opened exactly like the SSO landing: refresh channel
      // first, then the profile.
      const token = await refreshAccessToken();
      if (!token) {
        throw new Error("Local login succeeded but the refresh channel returned no access token");
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
      error,
      login,
      loginWithPassword,
      logout,
      refreshToken,
    }),
    [user, tenant, isLoading, error, login, loginWithPassword, logout, refreshToken],
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
