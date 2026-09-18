"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Bot, Search } from "lucide-react";

import { AgentCard } from "@/components/agents/agent-card";
import { useRole } from "@/components/auth/role-context";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { deriveCategories, filterAgents } from "@/lib/agent-filter";
import { ROUTES } from "@/lib/api-routes";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { canAccessAdmin } from "@/lib/roles";
import { useAllAgents } from "@/lib/use-agents";
import { cn } from "@/lib/utils";

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} className="flex flex-col gap-3 rounded-lg border border-border p-5">
          <div className="flex items-center gap-3">
            <Skeleton className="h-11 w-11 rounded-lg" />
            <div className="flex flex-1 flex-col gap-2">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-3 w-1/3" />
            </div>
          </div>
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-4/5" />
        </div>
      ))}
    </div>
  );
}

function CategoryTag({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-1 text-sm font-medium transition-colors",
        active
          ? "border-primary bg-primary-subtle text-primary"
          : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      {label}
    </button>
  );
}

/** User-facing marketplace: published-agent grid with search + category filter. */
export function AgentMarketplace() {
  const router = useRouter();
  const role = useRole();
  const canOpenDetail = canAccessAdmin(role);
  const { data, isLoading, error } = useAllAgents();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string | null>(null);

  const agents = useMemo(() => data ?? [], [data]);
  const categories = useMemo(() => deriveCategories(agents), [agents]);
  const filtered = useMemo(
    () => filterAgents(agents, search, category),
    [agents, search, category],
  );

  // Employees have no user-facing detail page; only admin-capable roles may
  // click through to the management detail page.
  const openAgent = (agentId: string) => {
    if (canOpenDetail) {
      router.push(ROUTES.adminAgentDetail(agentId));
    }
  };

  if (isLoading) {
    return <SkeletonGrid />;
  }

  if (error) {
    return (
      <EmptyState
        title="加载失败"
        description={extractApiErrorMessage(error)}
        icon={<Bot className="h-6 w-6" />}
      />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-foreground">智能体广场</h1>
        <p className="text-sm text-muted-foreground">发现并使用已发布的智能体</p>
      </div>

      <div className="flex flex-col gap-3">
        <div className="relative max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索智能体名称或描述"
            className="pl-9"
            aria-label="搜索智能体"
          />
        </div>
        {categories.length > 0 && (
          <div className="flex flex-wrap gap-2" role="group" aria-label="分类筛选">
            <CategoryTag
              active={category === null}
              label="全部"
              onClick={() => setCategory(null)}
            />
            {categories.map((value) => (
              <CategoryTag
                key={value}
                active={category === value}
                label={value}
                onClick={() => setCategory(value)}
              />
            ))}
          </div>
        )}
      </div>

      {filtered.length === 0 ? (
        agents.length === 0 ? (
          <EmptyState
            icon={<Bot className="h-6 w-6" />}
            title="暂无已发布的智能体"
            description="智能体发布后会展现在这里，请联系管理员创建并发布"
          />
        ) : (
          <EmptyState
            icon={<Search className="h-6 w-6" />}
            title="未找到匹配结果"
            description="尝试调整搜索词或分类筛选"
          />
        )
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((agent) => (
            <AgentCard
              key={agent.agentId}
              agent={agent}
              onClick={canOpenDetail ? () => openAgent(agent.agentId) : undefined}
            />
          ))}
        </div>
      )}
    </div>
  );
}
