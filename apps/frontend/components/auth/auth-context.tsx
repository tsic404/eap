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
import type { ApiEnvelope, CurrentUser, Tenant } from "@/lib/auth-types";
import { apiClient, refreshAccessToken } from "@/lib/http-client";
import { clearAccessToken, getAccessToken } from "@/lib/token-store";

export interface AuthContextValue {
  user: CurrentUser | null;
  tenant: Tenant | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  error: string | null;
  login: (provider: string) => void;
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
      setUser(currentUser);
      setTenant(currentUser ? { id: currentUser.tenantId } : null);
      setError(null);
      return currentUser;
    } catch (err) {
      // A 401 here means "not authenticated", not an error worth surfacing.
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
        await refreshAccessToken();
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

  const login = useCallback((provider: string) => {
    window.location.assign(
      `${AUTH_ROUTES.login}?provider=${encodeURIComponent(provider)}`,
    );
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiClient.post(API_ROUTES.logout);
    } catch {
      // Local cleanup still runs even if the backend call fails.
    } finally {
      setUser(null);
      setTenant(null);
      setError(null);
      clearAccessToken();
      window.location.assign(ROUTES.login);
    }
  }, []);

  const refreshToken = useCallback(async () => {
    const token = await refreshAccessToken();
    if (token) {
      await loadUser();
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
      logout,
      refreshToken,
    }),
    [user, tenant, isLoading, error, login, logout, refreshToken],
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
