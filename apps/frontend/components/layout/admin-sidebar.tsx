import {
  LayoutDashboard,
  ScrollText,
  Server,
  Settings,
  ShieldCheck,
  Users,
} from "lucide-react";

import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { key: "dashboard", label: "仪表盘", icon: LayoutDashboard, href: "#" },
  { key: "users", label: "用户管理", icon: Users, href: "#" },
  { key: "review", label: "智能体审核", icon: ShieldCheck, href: "#" },
  { key: "gateway", label: "模型网关", icon: Server, href: "#" },
  { key: "audit", label: "审计日志", icon: ScrollText, href: "#" },
  { key: "settings", label: "系统设置", icon: Settings, href: "#" },
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
  return (
    <nav
      className={cn(
        "flex w-56 flex-col gap-1 bg-background p-3 md:w-16 lg:w-56",
        className,
      )}
      aria-label="管理工作区导航"
    >
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon;
        const isActive = item.key === active;
        return (
          <a
            key={item.key}
            href={item.href}
            aria-current={isActive ? "page" : undefined}
            title={item.label}
            className={cn(
              "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors md:justify-center lg:justify-start",
              isActive
                ? "bg-primary-subtle text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            <span className="md:hidden lg:inline">{item.label}</span>
          </a>
        );
      })}
    </nav>
  );
}
