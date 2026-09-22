"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";

import { DashboardSkeleton } from "@/components/dashboard/dashboard-skeleton";

// The dashboard is the heaviest client graph in the admin workspace (recharts
// plus a data hook per section). Mounting it only after the window `load` event
// keeps `/admin` navigation bound to the shell: the sidebar and header paint
// from the server-rendered layout, and a slow dashboard chunk can no longer
// hold the document's `load` event hostage or blank the whole tree when a
// dependency behind one section is unavailable.
const AdminDashboardPage = dynamic(
  () =>
    import("@/components/dashboard/admin-dashboard-page").then(
      (module) => module.AdminDashboardPage,
    ),
  { ssr: false, loading: () => <DashboardSkeleton /> },
);

// How long to wait for the window `load` event before mounting the dashboard
// anyway. Only reached when a subresource never settles; `load` normally fires
// first and this timer is cleared.
const DASHBOARD_MOUNT_FALLBACK_MS = 5000;

/** `/admin` landing route: shell first, dashboard body once the page has loaded. */
export default function AdminDashboardRoute() {
  const [showDashboard, setShowDashboard] = useState(false);

  useEffect(() => {
    let timer = 0;
    const mountDashboard = () => {
      window.clearTimeout(timer);
      setShowDashboard(true);
    };
    // Subscribe before checking `readyState`: a `load` that lands between the
    // check and the subscription would otherwise be missed and the body would
    // stall until the fallback timer fires.
    window.addEventListener("load", mountDashboard);
    if (document.readyState === "complete") {
      mountDashboard();
    } else {
      timer = window.setTimeout(mountDashboard, DASHBOARD_MOUNT_FALLBACK_MS);
    }
    return () => {
      window.removeEventListener("load", mountDashboard);
      window.clearTimeout(timer);
    };
  }, []);

  return showDashboard ? <AdminDashboardPage /> : <DashboardSkeleton />;
}
