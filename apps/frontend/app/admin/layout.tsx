"use client";

import { AdminRouteGuard } from "@/components/auth/admin-route-guard";

/**
 * Friendly client-side guard for every `/admin/*` route. Authorization is
 * enforced server-side in `middleware.ts`; this only renders the notice for
 * the hydrated client.
 */
export default function AdminLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <AdminRouteGuard>{children}</AdminRouteGuard>;
}
