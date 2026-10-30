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
  const { isAuthenticated, isPasswordChangeRequired, isLoading, isSessionUnresolved } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;
    if (isPasswordChangeRequired) {
      router.replace(ROUTES.password);
      return;
    }
    // A renewal that ended inside C-25's window without a token is not a
    // sign-out: the role cookie stays, recovery waits for the next document,
    // and this one deliberately retries nothing — another loop here would only
    // add conflict pressure inside the same window.
    if (isSessionUnresolved) return;
    if (!isAuthenticated) {
      router.replace(ROUTES.login);
    }
  }, [isAuthenticated, isLoading, isPasswordChangeRequired, isSessionUnresolved, router]);

  if (isLoading || isSessionUnresolved || !isAuthenticated) {
    return <CallbackHandler />;
  }

  return <>{children}</>;
}
