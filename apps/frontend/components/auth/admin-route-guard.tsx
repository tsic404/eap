"use client";

import { AdminForbidden } from "@/components/auth/admin-forbidden";
import { useRole } from "@/components/auth/role-context";
import { canAccessAdmin } from "@/lib/roles";

export interface AdminRouteGuardProps {
  children: React.ReactNode;
}

/**
 * Client-side friendly guard for the admin workspace. Server-side enforcement
 * lives in `middleware.ts`; this layer renders the permission notice (§31.2.2)
 * instead of the admin content when the current role is `employee`.
 */
export function AdminRouteGuard({ children }: AdminRouteGuardProps) {
  const role = useRole();
  if (!canAccessAdmin(role)) {
    return <AdminForbidden />;
  }
  return <>{children}</>;
}
