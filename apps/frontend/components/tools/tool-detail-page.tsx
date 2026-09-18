"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Trash2 } from "lucide-react";

import { ConfirmDialog } from "@/components/agents/confirm-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { ROUTES } from "@/lib/api-routes";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { deleteTool } from "@/lib/tool-service";
import { useTool } from "@/lib/use-tools";

import { DebugPanel } from "./debug-panel";
import { PermissionModeBadge, RiskLevelBadge } from "./tool-badges";
import { ToolConfigForm } from "./tool-config-form";
import { TOOL_STATUS_LABEL, TOOL_STATUS_VARIANT } from "./tool-labels";

function DetailSkeleton() {
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-8">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Skeleton className="h-9 w-9 rounded-md" />
          <div className="flex flex-col gap-2">
            <Skeleton className="h-6 w-40" />
            <Skeleton className="h-4 w-24" />
          </div>
        </div>
        <Skeleton className="h-5 w-24" />
      </div>
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

/** Admin tool detail: config form + debug panel + delete. */
export function ToolDetailPage() {
  const params = useParams<{ id: string }>();
  const toolId = params.id;
  const router = useRouter();
  const { toast } = useToast();

  const { data: tool, error, isLoading, mutate } = useTool(toolId);

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await deleteTool(toolId);
      toast({ type: "success", title: "工具已删除" });
      router.replace(ROUTES.adminTools);
    } catch (deleteError) {
      toast({
        type: "error",
        title: "删除失败",
        description: extractApiErrorMessage(deleteError),
      });
      setDeleting(false);
      setDeleteOpen(false);
    }
  };

  if (isLoading) {
    return <DetailSkeleton />;
  }

  if (error || !tool) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-8">
        <EmptyState
          title="加载失败"
          description={error ? extractApiErrorMessage(error) : "工具不存在"}
        />
      </div>
    );
  }

  const statusLabel = TOOL_STATUS_LABEL[tool.status] ?? tool.status;
  const statusVariant = TOOL_STATUS_VARIANT[tool.status] ?? "default";

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-8">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="h-9 w-9 p-0"
            onClick={() => router.push(ROUTES.adminTools)}
            aria-label="返回"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="flex flex-col">
            <h1 className="text-2xl font-bold text-foreground">{tool.name}</h1>
            <span className="text-sm text-muted-foreground">{tool.tool_id}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <RiskLevelBadge riskLevel={tool.risk_level} />
          <PermissionModeBadge permissionMode={tool.permission_mode} />
          <Badge variant={statusVariant}>{statusLabel}</Badge>
        </div>
      </div>

      <ToolConfigForm
        tool={tool}
        onSaved={(updated) => mutate(updated, { revalidate: false })}
      />

      <DebugPanel toolId={tool.tool_id} disabled={tool.status !== "active"} />

      <div className="rounded-lg border border-danger-subtle p-4">
        <p className="text-sm font-medium text-foreground">危险操作</p>
        <p className="mt-1 text-sm text-muted-foreground">
          删除后不可恢复。
        </p>
        <Button
          variant="outline"
          className="mt-3 text-danger hover:text-danger"
          onClick={() => setDeleteOpen(true)}
        >
          <Trash2 className="h-4 w-4" />
          删除工具
        </Button>
      </div>

      <ConfirmDialog
        open={deleteOpen}
        title="删除工具"
        description={`确定要删除工具「${tool.name}」吗？此操作不可撤销。`}
        confirmLabel="删除"
        danger
        loading={deleting}
        onCancel={() => setDeleteOpen(false)}
        onConfirm={handleDelete}
      />
    </div>
  );
}
