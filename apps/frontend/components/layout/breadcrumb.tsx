"use client";

import { ChevronRight } from "lucide-react";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

const SEGMENT_LABEL_KEYS: Record<string, string> = {
  user: "user",
  agents: "agents",
  knowledge: "knowledge",
  tools: "tools",
  conversations: "conversations",
  settings: "settings",
  admin: "admin",
  tasks: "tasks",
};

/** Path-derived breadcrumb: `首页 / <segment…>`. */
export function Breadcrumb() {
  const pathname = usePathname() ?? "/";
  const t = useTranslations("breadcrumb");
  const segments = pathname.split("/").filter(Boolean);
  const crumbs = segments.map((segment, index) => ({
    path: `/${segments.slice(0, index + 1).join("/")}`,
    label:
      SEGMENT_LABEL_KEYS[segment] !== undefined
        ? t(SEGMENT_LABEL_KEYS[segment])
        : segment,
  }));

  return (
    <nav aria-label={t("navLabel")} className="flex items-center gap-1 text-sm">
      <span className="text-muted-foreground">{t("home")}</span>
      {crumbs.map((crumb, index) => (
        <span key={crumb.path} className="flex items-center gap-1">
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
          <span
            className={
              index === crumbs.length - 1
                ? "font-medium text-foreground"
                : "text-muted-foreground"
            }
          >
            {crumb.label}
          </span>
        </span>
      ))}
    </nav>
  );
}
