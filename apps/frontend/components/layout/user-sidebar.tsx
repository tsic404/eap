import { BookOpen, Bot, ListTodo, MessageSquare, Wrench } from "lucide-react";

import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { key: "chat", label: "会话", icon: MessageSquare, href: "#" },
  { key: "agents", label: "智能体", icon: Bot, href: "#" },
  { key: "knowledge", label: "知识库", icon: BookOpen, href: "#" },
  { key: "tools", label: "工具", icon: Wrench, href: "#" },
  { key: "tasks", label: "任务中心", icon: ListTodo, href: "#" },
] as const;

export interface UserSidebarProps {
  active?: string;
  className?: string;
}

/** Navigation sidebar for the user workspace. */
export function UserSidebar({ active, className }: UserSidebarProps) {
  return (
    <nav
      className={cn("flex w-56 flex-col gap-1 border-r border-border bg-background p-3", className)}
      aria-label="用户工作区导航"
    >
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon;
        const isActive = item.key === active;
        return (
          <a
            key={item.key}
            href={item.href}
            aria-current={isActive ? "page" : undefined}
            className={cn(
              "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              isActive
                ? "bg-primary-subtle text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4" />
            {item.label}
          </a>
        );
      })}
    </nav>
  );
}
