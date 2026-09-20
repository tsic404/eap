"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Wifi } from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

const RECOVERY_BANNER_DURATION_MS = 2000;

type NetworkState = "online" | "offline" | "recovered";

/**
 * Fixed top banner reflecting connectivity (§31.2.8): yellow while offline,
 * green for a short "recovered" pulse. Data refresh on reconnect is left to
 * SWR's default `revalidateOnReconnect`, which revalidates only mounted keys
 * instead of re-fetching the whole cache in one burst.
 */
export function OfflineBanner() {
  const [state, setState] = useState<NetworkState>("online");
  const recoveryTimerRef = useRef<number | null>(null);
  const t = useTranslations("offline");

  useEffect(() => {
    // Sync the initial state after hydration so SSR markup always matches.
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      setState("offline");
    }

    const handleOffline = () => {
      if (recoveryTimerRef.current !== null) {
        window.clearTimeout(recoveryTimerRef.current);
        recoveryTimerRef.current = null;
      }
      setState("offline");
    };
    const handleOnline = () => {
      setState("recovered");
      if (recoveryTimerRef.current !== null) {
        window.clearTimeout(recoveryTimerRef.current);
      }
      recoveryTimerRef.current = window.setTimeout(() => {
        recoveryTimerRef.current = null;
        setState("online");
      }, RECOVERY_BANNER_DURATION_MS);
    };

    window.addEventListener("offline", handleOffline);
    window.addEventListener("online", handleOnline);
    return () => {
      window.removeEventListener("offline", handleOffline);
      window.removeEventListener("online", handleOnline);
      if (recoveryTimerRef.current !== null) {
        window.clearTimeout(recoveryTimerRef.current);
      }
    };
  }, []);

  if (state === "online") return null;

  const isDisconnected = state === "offline";

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "fixed inset-x-0 top-0 z-50 flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium",
        isDisconnected
          ? "bg-warning-subtle text-warning"
          : "bg-success-subtle text-success",
      )}
    >
      {isDisconnected ? (
        <>
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          {t("disconnected")}
        </>
      ) : (
        <>
          <Wifi className="h-4 w-4" aria-hidden="true" />
          {t("recovered")}
        </>
      )}
    </div>
  );
}
