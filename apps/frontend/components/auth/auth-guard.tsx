"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { ROUTES } from "@/lib/api-routes";
import { useAuth } from "./auth-context";
import { CallbackHandler } from "./callback-handler";

/** Blocks unauthenticated access, redirecting to the login page. */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.replace(ROUTES.login);
    }
  }, [isAuthenticated, isLoading, router]);

  if (isLoading || !isAuthenticated) {
    return <CallbackHandler />;
  }

  return <>{children}</>;
}
