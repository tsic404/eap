"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { ROUTES } from "@/lib/api-routes";
import { useAuth } from "./auth-context";
import { CallbackHandler } from "./callback-handler";

/**
 * Blocks unauthenticated access, redirecting to the login page.
 *
 * An account the backend refuses pending a password change is guided to the
 * password page instead: its profile never loads, so the login redirect would
 * otherwise win the race against the guidance (C-15 / C-113).
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isPasswordChangeRequired, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;
    if (isPasswordChangeRequired) {
      router.replace(ROUTES.password);
      return;
    }
    if (!isAuthenticated) {
      router.replace(ROUTES.login);
    }
  }, [isAuthenticated, isLoading, isPasswordChangeRequired, router]);

  if (isLoading || !isAuthenticated) {
    return <CallbackHandler />;
  }

  return <>{children}</>;
}
