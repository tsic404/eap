"use client";

import { useEffect, useState } from "react";

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { CallbackHandler } from "@/components/auth/callback-handler";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ROUTES } from "@/lib/api-routes";
import { refreshAccessToken } from "@/lib/refresh-channel";
import { getAccessToken } from "@/lib/token-store";

import { PasswordChangeForm } from "./password-change-form";

/**
 * Password page for a signed-in account (C-15 / C-113).
 *
 * The gate proves the session through the refresh channel, not the profile
 * endpoint: a forced-change account's `/api/me` is itself refused with
 * `PASSWORD_CHANGE_REQUIRED`, and this page is where that account must land.
 * No capability read point is consulted — a closed local-login switch never
 * removes the signed-in account's own password change (C-110).
 */
export function PasswordChangePage() {
  const router = useRouter();
  const t = useTranslations("password");
  const [isCheckingSession, setIsCheckingSession] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let token = getAccessToken();
      if (!token) {
        token = await refreshAccessToken();
      }
      if (cancelled) return;
      if (!token) {
        router.replace(ROUTES.login);
        return;
      }
      setIsCheckingSession(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (isCheckingSession) {
    return <CallbackHandler />;
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted px-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("subtitle")}</CardDescription>
        </CardHeader>
        <CardContent>
          <PasswordChangeForm />
        </CardContent>
      </Card>
    </main>
  );
}
