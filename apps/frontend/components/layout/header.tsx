"use client";

import { Bell, LogOut, Settings, User } from "lucide-react";

import { useAuth } from "@/components/auth/auth-context";
import { useRole } from "@/components/auth/role-context";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { WorkspaceSwitcher } from "@/components/layout/workspace-switcher";
import { Button } from "@/components/ui/button";
import { Dropdown, type DropdownItem } from "@/components/ui/dropdown";

/** Top application bar: logo, breadcrumb, notifications, user menu. */
export function Header() {
  const { user, logout } = useAuth();
  const role = useRole();

  const avatarText = user?.avatarText ?? user?.name.slice(0, 1) ?? "用";
  const displayName = user?.name ?? "用户";

  const menuItems: DropdownItem[] = [
    { value: "profile", label: "个人资料", icon: <User className="h-4 w-4" /> },
    { value: "settings", label: "设置", icon: <Settings className="h-4 w-4" /> },
    {
      value: "logout",
      label: "登出",
      icon: <LogOut className="h-4 w-4" />,
      danger: true,
      onSelect: () => {
        void logout();
      },
    },
  ];

  return (
    <header className="flex h-14 items-center gap-3 border-b border-border bg-background px-4">
      <span className="text-sm font-semibold text-foreground">EAP</span>
      <Breadcrumb />
      <WorkspaceSwitcher role={role} />
      <div className="ml-auto flex items-center gap-1">
        <Button variant="ghost" size="sm" className="h-9 w-9 p-0" aria-label="通知">
          <Bell className="h-4 w-4" />
        </Button>
        <Dropdown
          align="right"
          triggerClassName="rounded-full"
          trigger={
            <span
              className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-sm font-medium text-primary-foreground"
              title={displayName}
            >
              {avatarText}
            </span>
          }
          items={menuItems}
        />
      </div>
    </header>
  );
}
