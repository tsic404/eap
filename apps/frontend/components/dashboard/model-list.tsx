"use client";

import { Server } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { formatNumber } from "@/lib/formatters";
import type { ModelProvider } from "@/lib/dashboard-types";

export interface ModelListProps {
  providers: ModelProvider[];
}

const DEPLOYMENT_LABELS: Record<string, string> = {
  custom: "自定义",
  system: "系统内置",
  model: "托管模型",
};

/** Model providers proxied from the Dify Console (`GET /api/models`). */
export function ModelList({ providers }: ModelListProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>模型供应商</CardTitle>
        <CardDescription>Dify Console 代理的模型供应商</CardDescription>
      </CardHeader>
      {providers.length === 0 ? (
        <CardContent>
          <EmptyState title="暂无模型" description="未配置任何模型供应商" />
        </CardContent>
      ) : (
        <CardContent className="flex flex-col gap-1">
          {providers.map((provider) => {
            const deploymentLabel =
              DEPLOYMENT_LABELS[provider.deploymentType ?? ""] ??
              provider.deploymentType ??
              "未知";
            return (
              <div
                key={provider.provider}
                className="flex items-center gap-3 rounded-md px-2 py-2"
              >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-subtle text-primary">
                  <Server className="h-5 w-5" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-foreground">
                    {provider.label ?? provider.provider}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {provider.provider}
                  </p>
                </div>
                <Badge variant="info">{deploymentLabel}</Badge>
                <span className="shrink-0 text-sm text-muted-foreground">
                  {formatNumber(provider.modelCount)} 个模型
                </span>
              </div>
            );
          })}
        </CardContent>
      )}
    </Card>
  );
}
