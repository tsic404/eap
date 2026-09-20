import type { Metadata } from "next";
import { cookies } from "next/headers";

import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages, getTranslations } from "next-intl/server";
import { SWRConfig } from "swr";

import { AuthProvider } from "@/components/auth/auth-context";
import { RoleProvider } from "@/components/auth/role-context";
import { WebVitals } from "@/components/layout/web-vitals";
import { OfflineBanner } from "@/components/ui/offline-banner";
import { ToastProvider } from "@/components/ui/toast";
import { resolveRole, ROLE_COOKIE } from "@/lib/roles";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("metadata");
  return {
    title: t("title"),
    description: "Enterprise Agent Platform",
  };
}

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const role = resolveRole((await cookies()).get(ROLE_COOKIE)?.value);
  const locale = await getLocale();
  const messages = await getMessages();

  return (
    <html lang={locale}>
      <body>
        <NextIntlClientProvider locale={locale} messages={messages}>
          <SWRConfig
            value={{
              // Coalesce bursts of the same fetch into one request (§30.14).
              dedupingInterval: 2000,
            }}
          >
            <AuthProvider>
              <RoleProvider role={role}>
                <ToastProvider>
                  <WebVitals />
                  <OfflineBanner />
                  {children}
                </ToastProvider>
              </RoleProvider>
            </AuthProvider>
          </SWRConfig>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
