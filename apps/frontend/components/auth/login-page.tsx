"use client";

import { useEffect, useState } from "react";

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DEFAULT_SSO_PROVIDER, ROUTES } from "@/lib/api-routes";
import { fetchAuthCapabilities } from "@/lib/auth-service";
import { useAuth } from "./auth-context";
import { LocalLoginForm } from "./local-login-form";

/** Centred login card: the enterprise entry, plus the local account form when enabled. */
export function LoginPage() {
  const { login, error, isLoading, isAuthenticated } = useAuth();
  const router = useRouter();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isLocalLoginEnabled, setIsLocalLoginEnabled] = useState(false);
  const [hasCallbackError, setHasCallbackError] = useState(false);
  const t = useTranslations("login");

  // C-24: the callback's failure code is machine-readable, not copy — every
  // code lands on one friendly line, and the card survives a failed profile
  // read instead of being replaced by its message.
  useEffect(() => {
    setHasCallbackError(new URLSearchParams(window.location.search).has("error"));
  }, []);

  // A live session skips the identity provider entirely (AC-02): bootstrap
  // already proved it through the same refresh channel, so this only follows
  // its settled answer.
  useEffect(() => {
    if (isLoading || !isAuthenticated) return;
    router.replace(ROUTES.home);
  }, [isAuthenticated, isLoading, router]);

  // The capability read point is the only switch (C-110). A read point that
  // cannot be reached leaves the form hidden rather than offering an entry the
  // deployment may not serve.
  useEffect(() => {
    let cancelled = false;
    fetchAuthCapabilities()
      .then((capabilities) => {
        if (!cancelled) setIsLocalLoginEnabled(capabilities.localLoginEnabled);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLogin = () => {
    setIsSubmitting(true);
    login(DEFAULT_SSO_PROVIDER);
  };

  const displayError = hasCallbackError ? t("errors.callbackFailed") : error;

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted px-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("subtitle")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Button
            size="lg"
            loading={isSubmitting}
            onClick={handleLogin}
            className="w-full"
          >
            {t("sso")}
          </Button>
          {isLocalLoginEnabled && <LocalLoginForm />}
          {displayError && (
            <p role="alert" className="text-sm text-danger">
              {displayError}
            </p>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
