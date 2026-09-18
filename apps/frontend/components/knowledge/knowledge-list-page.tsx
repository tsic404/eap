"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";

import { KBCard } from "@/components/knowledge/kb-card";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { ROUTES } from "@/lib/api-routes";
import { createKnowledgeBase } from "@/lib/knowledge-service";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { useKnowledgeBases } from "@/lib/use-knowledge";

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }, (_, index) => (
        <Skeleton key={index} className="h-40 w-full" />
      ))}
    </div>
  );
}

/** Admin knowledge-base listing with a create dialog. */
export function KnowledgeListPage() {
  const router = useRouter();
  const { toast } = useToast();
  const { data, error, isLoading, mutate } = useKnowledgeBases();

  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [type, setType] = useState("business");
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    const trimmedName = name.trim();
    if (trimmedName === "") {
      toast({ type: "error", title: "请输入知识库名称" });
      return;
    }
    setCreating(true);
    try {
      await createKnowledgeBase({
        name: trimmedName,
        description: description.trim() === "" ? null : description.trim(),
        type,
      });
      toast({ type: "success", title: "知识库已创建" });
      setCreateOpen(false);
      setName("");
      setDescription("");
      setType("business");
      await mutate();
    } catch (createError) {
      toast({
        type: "error",
        title: "创建失败",
        description: extractApiErrorMessage(createError),
      });
    } finally {
      setCreating(false);
    }
  };

  if (isLoading) {
    return (
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-8">
        <Skeleton className="h-10 w-48" />
        <SkeletonGrid />
      </div>
    );
  }

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

  const items = data?.items ?? [];

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">知识库</h1>
          <p className="text-sm text-muted-foreground">管理与测试知识库召回</p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          新建知识库
        </Button>
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="暂无知识库"
          description="创建第一个知识库以开始"
          action={
            <Button onClick={() => setCreateOpen(true)}>新建知识库</Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((kb) => (
            <KBCard
              key={kb.kb_id}
              knowledgeBase={kb}
              onClick={() => router.push(ROUTES.adminKnowledgeDetail(kb.kb_id))}
            />
          ))}
        </div>
      )}

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="新建知识库"
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => setCreateOpen(false)}
              disabled={creating}
            >
              取消
            </Button>
            <Button onClick={handleCreate} loading={creating}>
              创建
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <Input
            label="名称"
            placeholder="例如：产品手册知识库"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <Input
            label="描述"
            placeholder="可选"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium text-foreground">类型</label>
            <select
              value={type}
              onChange={(event) => setType(event.target.value)}
              className="h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="business">业务</option>
            </select>
          </div>
        </div>
      </Modal>
    </div>
  );
}
