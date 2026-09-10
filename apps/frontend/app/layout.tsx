import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "EAP — 企业智能体平台",
  description: "Enterprise Agent Platform",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
