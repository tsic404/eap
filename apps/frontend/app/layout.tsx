import type { Metadata } from "next";
import { cookies } from "next/headers";

import { RoleProvider } from "@/components/auth/role-context";
import { resolveRole, ROLE_COOKIE } from "@/lib/roles";
import "./globals.css";

export const metadata: Metadata = {
  title: "EAP — 企业智能体平台",
  description: "Enterprise Agent Platform",
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const role = resolveRole((await cookies()).get(ROLE_COOKIE)?.value);

  return (
    <html lang="zh-CN">
      <body>
        <RoleProvider role={role}>{children}</RoleProvider>
      </body>
    </html>
  );
}
