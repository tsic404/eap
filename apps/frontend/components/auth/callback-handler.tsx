"use client";

import { Spinner } from "@/components/ui/spinner";
import { useTranslations } from "next-intl";

/** Full-screen loading state shown while the OIDC callback resolves. */
export function CallbackHandler() {
  const t = useTranslations("callback");

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3">
      <Spinner size="lg" className="text-primary" />
      <p className="text-sm text-muted-foreground">{t("loading")}</p>
    </div>
  );
}
