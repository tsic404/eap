"use client";

import { Activity, AlertTriangle, Bot, Clock } from "lucide-react";

import { AlertList } from "@/components/dashboard/alert-list";
import { HealthStatusPanel } from "@/components/dashboard/health-status-panel";
import { MetricCard } from "@/components/dashboard/metric-card";
import { ModelList } from "@/components/dashboard/model-list";
import { TopAgentsList } from "@/components/dashboard/top-agents-list";
import { TrendChart } from "@/components/dashboard/trend-chart";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { evaluateDashboardHealth, formatPercent } from "@/lib/dashboard-health";
import { formatDuration, formatNumber } from "@/lib/formatters";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { useAdminDashboard, useModels } from "@/lib/use-dashboard";

function DashboardSkeleton() {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-8">
      <Skeleton className="h-10 w-32" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-20 w-full" />
        ))}
      </div>
      <Skeleton className="h-72 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

function ModelsSection() {
  const { data, error, isLoading, mutate } = useModels();

  if (isLoading) return <Skeleton className="h-40 w-full" />;
  if (error) {
    return (
      <ErrorBanner
        description={extractApiErrorMessage(error)}
        onRetry={() => mutate()}
      />
    );
  }
  return <ModelList providers={data ?? []} />;
}

/** Admin dashboard: metrics, trend, health, top agents, alerts, models. */
export function AdminDashboardPage() {
  const { data, error, isLoading, mutate } = useAdminDashboard();

  if (isLoading) return <DashboardSkeleton />;

  if (error) {
    return (
      <div className="mx-auto max-w-6xl px-6 py-8">
        <ErrorBanner
          description={extractApiErrorMessage(error)}
          onRetry={() => mutate()}
        />
      </div>
    );
  }

  const dashboard = data!;
  const health = evaluateDashboardHealth(dashboard.metrics);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-8">
      <header>
        <h1 className="text-2xl font-bold text-foreground">仪表盘</h1>
        <p className="text-sm text-muted-foreground">
          核心指标、调用趋势与系统健康
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label="智能体总数"
          value={formatNumber(dashboard.metrics.totalAgents)}
          icon={Bot}
        />
        <MetricCard
          label="今日调用"
          value={formatNumber(dashboard.metrics.todayCalls)}
          icon={Activity}
        />
        <MetricCard
          label="平均耗时"
          value={formatDuration(dashboard.metrics.avgLatencyMs)}
          icon={Clock}
        />
        <MetricCard
          label="错误率"
          value={formatPercent(dashboard.metrics.errorRate)}
          icon={AlertTriangle}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <TrendChart points={dashboard.trend} />
        </div>
        <HealthStatusPanel level={health.level} checks={health.checks} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <TopAgentsList agents={dashboard.topAgents} />
        <AlertList alerts={health.alerts} />
      </div>

      <ModelsSection />
    </div>
  );
}
