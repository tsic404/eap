"use client";

import Link from "next/link";

import { useTranslations } from "next-intl";

/**
 * Friendly permission notice shown to employees who reach the admin workspace
 * (architecture v8.0 §31.2.2). A client component so both the server-rendered
 * `/forbidden` route and the client `AdminRouteGuard` can render it.
 */
export function AdminForbidden() {
  const t = useTranslations("forbidden");

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="text-2xl font-bold text-foreground">{t("title")}</h1>
      <p className="max-w-md text-sm text-muted-foreground">{t("description")}</p>
      <Link
        href="/user"
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary-hover"
      >
        {t("back")}
      </Link>
    </main>
  );
}
