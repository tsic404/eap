"use client";

import Link from "next/link";
import { BookOpen, Bot, ListTodo, MessageSquare, Wrench } from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { key: "chat", labelKey: "chat", icon: MessageSquare, href: "#" },
  { key: "agents", labelKey: "agents", icon: Bot, href: "#" },
  { key: "knowledge", labelKey: "knowledge", icon: BookOpen, href: "#" },
  { key: "tools", labelKey: "tools", icon: Wrench, href: "#" },
  { key: "tasks", labelKey: "tasks", icon: ListTodo, href: "/user/tasks" },
] as const;

export interface UserSidebarProps {
  active?: string;
  className?: string;
}

/**
 * Navigation sidebar for the user workspace. Collapses to icon-only on tablet
 * widths (`md`) via CSS breakpoints; labels return at `lg`.
 */
export function UserSidebar({ active, className }: UserSidebarProps) {
  const t = useTranslations("sidebar");

  return (
    <nav
      className={cn(
        "flex w-56 flex-col gap-1 bg-background p-3 md:w-16 lg:w-56",
        className,
      )}
      aria-label={t("userNav")}
    >
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon;
        const isActive = item.key === active;
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
