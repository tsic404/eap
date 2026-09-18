"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Trash2 } from "lucide-react";

import { AgentConfigPanel } from "@/components/agents/agent-config-panel";
import {
  AGENT_STATUS_LABEL,
  AGENT_STATUS_VARIANT,
  AGENT_TYPE_LABEL,
} from "@/components/agents/agent-labels";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { ROUTES } from "@/lib/api-routes";
import {
  deleteAgent,
  extractApiErrorMessage,
  isConflictError,
  offlineAgent,
  publishAgent,
} from "@/lib/platform-service";
import { useAgent } from "@/lib/use-agents";

import { ConfirmDialog } from "./confirm-dialog";
import { ConflictDialog } from "./conflict-dialog";

type ConfirmAction = "publish" | "offline" | "delete";

function DetailSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Skeleton className="h-12 w-12 rounded-lg" />
          <div className="flex flex-col gap-2">
            <Skeleton className="h-6 w-40" />
            <Skeleton className="h-4 w-24" />
          </div>
        </div>
        <Skeleton className="h-10 w-40" />
      </div>
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

/** Admin agent detail: lifecycle actions + 配置/调试/日志 panel. */
export function AgentDetailPage() {
  const params = useParams<{ id: string }>();
  const agentId = params.id;
  const router = useRouter();
  const { toast } = useToast();

  const { data: agent, error, isLoading, mutate } = useAgent(agentId);

  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  const [conflictOpen, setConflictOpen] = useState(false);
  const [pending, setPending] = useState(false);

  const handleAction = async (action: ConfirmAction) => {
    if (!agent) return;
    setPending(true);
    try {
      switch (action) {
        case "publish":
          await publishAgent(agent.agentId, agent.version);
          toast({ type: "success", title: "已发布" });
          break;
        case "offline":
          await offlineAgent(agent.agentId);
          toast({ type: "success", title: "已下线" });
          break;
        case "delete":
          await deleteAgent(agent.agentId);
          toast({ type: "success", title: "已删除" });
          router.replace(ROUTES.adminHome);
          return;
      }
      await mutate();
    } catch (error) {
      if (isConflictError(error)) {
        setConflictOpen(true);
      } else {
        toast({
          type: "error",
          title: "操作失败",
          description: extractApiErrorMessage(error),
        });
      }
    } finally {
      setPending(false);
      setConfirmAction(null);
    }
  };

  if (isLoading) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-8">
        <DetailSkeleton />
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-8">
        <EmptyState
          title="加载失败"
          description={error ? extractApiErrorMessage(error) : "智能体不存在"}
        />
      </div>
    );
  }

  const statusLabel = AGENT_STATUS_LABEL[agent.status] ?? agent.status;
  const statusVariant = AGENT_STATUS_VARIANT[agent.status] ?? "default";
  const isPublished = agent.status === "published";

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-8">
      <button
        type="button"
        onClick={() => router.replace(ROUTES.adminHome)}
        className="flex items-center gap-1 self-start text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
        返回管理端
      </button>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="flex h-12 w-12 items-center justify-center rounded-lg bg-muted text-2xl leading-none">
            {agent.icon ?? "🤖"}
          </span>
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-bold text-foreground">{agent.name}</h1>
              <Badge variant={statusVariant}>{statusLabel}</Badge>
            </div>
            <span className="text-sm text-muted-foreground">
              {AGENT_TYPE_LABEL[agent.type] ?? agent.type}
            </span>
          </div>
        </div>
        <div className="flex gap-2">
          {isPublished ? (
            <Button
              variant="outline"
              disabled={pending}
              onClick={() => setConfirmAction("offline")}
            >
              下线
            </Button>
          ) : (
            <Button disabled={pending} onClick={() => setConfirmAction("publish")}>
              发布
            </Button>
          )}
          <Button
            variant="outline"
            disabled={pending}
            onClick={() => setConfirmAction("delete")}
          >
            <Trash2 className="h-4 w-4" />
            删除
          </Button>
        </div>
      </div>

      <AgentConfigPanel agent={agent} />

      <ConfirmDialog
        open={confirmAction === "publish"}
        title="确认发布"
        description={`发布后「${agent.name}」将展示在智能体广场，供所有用户使用。`}
        confirmLabel="发布"
        loading={pending}
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => void handleAction("publish")}
      />
      <ConfirmDialog
        open={confirmAction === "offline"}
        title="确认下线"
        description={`下线后「${agent.name}」将从智能体广场移除，用户将无法继续使用。`}
        confirmLabel="下线"
        loading={pending}
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => void handleAction("offline")}
      />
      <ConfirmDialog
        open={confirmAction === "delete"}
        title="确认删除"
        description={`删除「${agent.name}」后不可恢复，请确认。`}
        confirmLabel="删除"
        danger
        loading={pending}
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => void handleAction("delete")}
      />
      <ConflictDialog
        open={conflictOpen}
        onCancel={() => setConflictOpen(false)}
        onReload={() => {
          setConflictOpen(false);
          void mutate();
        }}
      />
    </div>
  );
}
