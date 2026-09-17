"use client";

import { ChevronRight } from "lucide-react";
import { usePathname } from "next/navigation";

const SEGMENT_LABELS: Record<string, string> = {
  user: "用户工作区",
  agents: "智能体",
  knowledge: "知识库",
  tools: "工具",
  conversations: "会话",
  settings: "设置",
};

/** Path-derived breadcrumb: `首页 / <segment…>`. */
export function Breadcrumb() {
  const pathname = usePathname() ?? "/";
  const segments = pathname.split("/").filter(Boolean);
  const crumbs = segments.map((segment, index) => ({
    path: `/${segments.slice(0, index + 1).join("/")}`,
    label: SEGMENT_LABELS[segment] ?? segment,
  }));

  return (
    <nav aria-label="面包屑" className="flex items-center gap-1 text-sm">
      <span className="text-muted-foreground">首页</span>
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
