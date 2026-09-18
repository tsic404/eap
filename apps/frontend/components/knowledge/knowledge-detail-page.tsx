"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { ROUTES } from "@/lib/api-routes";
import { formatDateTime } from "@/lib/formatters";
import {
  deleteKnowledgeBase,
  listAgentsBoundToKnowledge,
} from "@/lib/knowledge-service";
import type { KnowledgeBase } from "@/lib/knowledge-types";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { useDocuments, useKnowledgeBase } from "@/lib/use-knowledge";

import { DocumentList } from "./document-list";
import { DocumentUploader } from "./document-uploader";
import { INDEXING_STATUS_LABEL, INDEXING_STATUS_VARIANT } from "./kb-labels";
import { RetrievalTest } from "./retrieval-test";

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

function DetailItem({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-sm text-foreground">{children}</span>
    </div>
  );
}

function SettingsTab({
  kb,
  onDelete,
}: {
  kb: KnowledgeBase;
  onDelete: () => void;
}) {
  const status = kb.indexing_status ?? "ready";
  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <DetailItem label="名称">{kb.name}</DetailItem>
        <DetailItem label="类型">{kb.type}</DetailItem>
        <DetailItem label="文档数">{kb.doc_count}</DetailItem>
        <DetailItem label="分块数">{kb.chunk_count}</DetailItem>
        <DetailItem label="索引状态">
          {INDEXING_STATUS_LABEL[status] ?? status}
        </DetailItem>
        <DetailItem label="创建时间">{formatDateTime(kb.created_at)}</DetailItem>
        {kb.description && (
          <DetailItem label="描述">{kb.description}</DetailItem>
        )}
      </div>

      <div className="rounded-lg border border-danger-subtle p-4">
        <p className="text-sm font-medium text-foreground">危险操作</p>
        <p className="mt-1 text-sm text-muted-foreground">
          删除后不可恢复；绑定中的智能体需先解除绑定。
        </p>
        <Button
          variant="outline"
          className="mt-3 text-danger hover:text-danger"
          onClick={onDelete}
        >
          <Trash2 className="h-4 w-4" />
          删除知识库
        </Button>
      </div>
    </div>
  );
}

/** Admin knowledge-base detail: 文档列表 / 召回测试 / 设置 tabs + delete. */
export function KnowledgeDetailPage() {
  const params = useParams<{ id: string }>();
  const kbId = params.id;
  const router = useRouter();
  const { toast } = useToast();

  const { data: kb, error: kbError, isLoading: kbLoading } =
    useKnowledgeBase(kbId);
  const {
    data: documentsData,
    error: documentsError,
    isLoading: documentsLoading,
    mutate: refreshDocuments,
  } = useDocuments(kbId);

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [boundAgents, setBoundAgents] = useState<string[] | null>(null);
  const [boundAgentsError, setBoundAgentsError] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const hasIndexing =
    documentsData?.items.some((document) => document.status === "indexing") ??
    false;

  const openDelete = async () => {
    setDeleteOpen(true);
    setBoundAgents(null);
    setBoundAgentsError(false);
    try {
      setBoundAgents(await listAgentsBoundToKnowledge(kbId));
    } catch {
      // A failed binding check must not read as "no bindings": treat it as an
      // unverifiable state and block deletion rather than enabling a
      // destructive action whose safety condition could not be confirmed.
      setBoundAgents([]);
      setBoundAgentsError(true);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await deleteKnowledgeBase(kbId);
      toast({ type: "success", title: "知识库已删除" });
      router.replace(ROUTES.adminKnowledge);
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

  if (kbLoading) {
    return <DetailSkeleton />;
  }

  if (kbError || !kb) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-8">
        <EmptyState
          title="加载失败"
          description={kbError ? extractApiErrorMessage(kbError) : "知识库不存在"}
        />
      </div>
    );
  }

  const tabs = [
    {
      value: "documents",
      label: "文档列表",
      content: (
        <div className="flex flex-col gap-6">
          <DocumentUploader kbId={kbId} onUploaded={refreshDocuments} />
          <DocumentList
            documents={documentsData?.items}
            isLoading={documentsLoading}
            error={documentsError}
            onRefresh={refreshDocuments}
          />
        </div>
      ),
    },
    {
      value: "retrieval",
      label: "召回测试",
      content: <RetrievalTest kbId={kbId} disabled={hasIndexing} />,
    },
    {
      value: "settings",
      label: "设置",
      content: <SettingsTab kb={kb} onDelete={openDelete} />,
    },
  ];

  const status = kb.indexing_status ?? "ready";
  const statusLabel = INDEXING_STATUS_LABEL[status] ?? status;
  const statusVariant = INDEXING_STATUS_VARIANT[status] ?? "default";

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 px-6 py-8">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="h-9 w-9 p-0"
            onClick={() => router.push(ROUTES.adminKnowledge)}
            aria-label="返回"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="flex flex-col">
            <h1 className="text-2xl font-bold text-foreground">{kb.name}</h1>
            <span className="text-sm text-muted-foreground">{kb.kb_id}</span>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-sm text-muted-foreground">
            文档 {kb.doc_count}
          </span>
          <span className="text-sm text-muted-foreground">
            分块 {kb.chunk_count}
          </span>
          <Badge variant={statusVariant}>{statusLabel}</Badge>
        </div>
      </div>

      {hasIndexing && (
        <div className="flex flex-col gap-2 rounded-lg border border-border bg-info-subtle p-4">
          <p className="text-sm font-medium text-foreground">正在索引文档…</p>
          <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
            <div className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
          </div>
          <p className="text-xs text-muted-foreground">
            索引完成前，召回测试暂不可用。
          </p>
        </div>
      )}

      <Tabs items={tabs} defaultValue="documents" />

      <Modal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title="删除知识库"
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => setDeleteOpen(false)}
              disabled={deleting}
            >
              取消
            </Button>
            {boundAgents !== null &&
              !boundAgentsError &&
              boundAgents.length === 0 && (
                <Button
                  onClick={handleDelete}
                  loading={deleting}
                  className="bg-danger text-white hover:bg-danger/90 active:bg-danger"
                >
                  删除
                </Button>
              )}
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            确定要删除知识库「{kb.name}」吗？此操作不可撤销。
          </p>
          {boundAgents === null ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner size="sm" />
              正在检查绑定智能体…
            </div>
          ) : boundAgentsError ? (
            <div className="flex flex-col gap-3 rounded-md border border-border bg-danger-subtle p-3">
              <p className="text-sm text-danger">
                无法确认绑定智能体，已禁用删除以防误删。
              </p>
              <Button
                variant="outline"
                size="sm"
                className="self-start"
                onClick={openDelete}
              >
                重试
              </Button>
            </div>
          ) : boundAgents.length > 0 ? (
            <div className="rounded-md border border-border bg-muted p-3">
              <p className="text-sm font-medium text-foreground">
                已绑定以下智能体：
              </p>
              <ul className="mt-1 list-inside list-disc text-sm text-muted-foreground">
                {boundAgents.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-danger">
                请先在智能体配置中解除绑定，再删除知识库。
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">未绑定任何智能体。</p>
          )}
        </div>
      </Modal>
    </div>
  );
}
