"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BookOpen,
  LayoutDashboard,
  ScrollText,
  Server,
  Settings,
  ShieldCheck,
  Users,
  Wrench,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { key: "dashboard", labelKey: "dashboard", icon: LayoutDashboard, href: "#" },
  { key: "knowledge", labelKey: "knowledge", icon: BookOpen, href: "/admin/knowledge" },
  { key: "tools", labelKey: "tools", icon: Wrench, href: "/admin/tools" },
  { key: "users", labelKey: "users", icon: Users, href: "#" },
  { key: "review", labelKey: "review", icon: ShieldCheck, href: "#" },
  { key: "runLogs", labelKey: "runLogs", icon: Activity, href: "/admin/run-logs" },
  { key: "gateway", labelKey: "gateway", icon: Server, href: "#" },
  { key: "audit", labelKey: "audit", icon: ScrollText, href: "#" },
  { key: "settings", labelKey: "settings", icon: Settings, href: "#" },
] as const;

export interface AdminSidebarProps {
  active?: string;
  className?: string;
}

/**
 * Navigation sidebar for the admin workspace. Collapses to icon-only on
 * tablet widths (`md`) via CSS breakpoints; labels return at `lg`.
 */
export function AdminSidebar({ active, className }: AdminSidebarProps) {
  const pathname = usePathname() ?? "";
  const t = useTranslations("sidebar");

  return (
    <nav
      className={cn(
        "flex w-56 flex-col gap-1 bg-background p-3 md:w-16 lg:w-56",
        className,
      )}
      aria-label={t("adminNav")}
    >
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon;
        const isActive =
          item.href !== "#"
            ? pathname.startsWith(item.href)
            : item.key === active;
        const label = t(item.labelKey);
        const classes = cn(
          "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors md:justify-center lg:justify-start",
          isActive
            ? "bg-primary-subtle text-primary"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        );
        const content = (
          <>
            <Icon className="h-4 w-4 shrink-0" />
            <span className="md:hidden lg:inline">{label}</span>
          </>
        );
        if (item.href === "#") {
          return (
            <a
              key={item.key}
              href={item.href}
              aria-current={isActive ? "page" : undefined}
              title={label}
              className={classes}
            >
              {content}
            </a>
          );
        }
        return (
          <Link
            key={item.key}
            href={item.href}
            aria-current={isActive ? "page" : undefined}
            title={label}
            className={classes}
          >
            {content}
          </Link>
        );
      })}
    </nav>
  );
}
