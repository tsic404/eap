"use client";

import { Bell, LogOut, Settings, User } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dropdown } from "@/components/ui/dropdown";
import { WorkspaceSwitcher } from "@/components/layout/workspace-switcher";

const USER_MENU_ITEMS = [
  { value: "profile", label: "个人资料", icon: <User className="h-4 w-4" /> },
  { value: "settings", label: "设置", icon: <Settings className="h-4 w-4" /> },
  { value: "logout", label: "退出登录", icon: <LogOut className="h-4 w-4" />, danger: true },
];

/** Top application bar: workspace switcher, notifications, user menu. */
export function Header() {
  return (
    <header className="flex h-14 items-center gap-3 border-b border-border bg-background px-4">
      <span className="text-sm font-semibold text-foreground">EAP</span>
      <WorkspaceSwitcher />
      <div className="ml-auto flex items-center gap-1">
        <Button variant="ghost" size="sm" className="h-9 w-9 p-0" aria-label="通知">
          <Bell className="h-4 w-4" />
        </Button>
        <Dropdown
          align="right"
          triggerClassName="rounded-full"
          trigger={
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-sm font-medium text-primary-foreground">
              管
            </span>
          }
          items={USER_MENU_ITEMS}
        />
      </div>
    </header>
  );
}
