"use client";

import { FileText } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import type { KnowledgeDocument } from "@/lib/knowledge-types";
import { extractApiErrorMessage } from "@/lib/platform-service";

import { INDEXING_STATUS_LABEL, INDEXING_STATUS_VARIANT } from "./kb-labels";

function DocumentListSkeleton() {
  return (
    <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
      {Array.from({ length: 3 }, (_, index) => (
        <li key={index} className="flex items-center gap-3 px-4 py-3">
          <Skeleton className="h-4 w-4" />
          <Skeleton className="h-4 flex-1" />
          <Skeleton className="h-5 w-16" />
        </li>
      ))}
    </ul>
  );
}

export interface DocumentListProps {
  documents: KnowledgeDocument[] | undefined;
  isLoading: boolean;
  error: unknown;
  onRefresh: () => void;
}

/** Document listing with per-document indexing status (indexing/failed UX). */
export function DocumentList({
  documents,
  isLoading,
  error,
  onRefresh,
}: DocumentListProps) {
  if (isLoading) {
    return <DocumentListSkeleton />;
  }

  if (error) {
    return (
      <ErrorBanner
        description={extractApiErrorMessage(error)}
        onRetry={onRefresh}
      />
    );
  }

  if (!documents || documents.length === 0) {
    return (
      <EmptyState
        title="暂无文档"
        description="上传文档后开始构建索引"
      />
    );
  }

  return (
    <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
      {documents.map((document) => {
        const label = INDEXING_STATUS_LABEL[document.status] ?? document.status;
        const variant = INDEXING_STATUS_VARIANT[document.status] ?? "default";
        return (
          <li
            key={document.id}
            className="flex items-center gap-3 px-4 py-3"
          >
            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate text-sm text-foreground">
              {document.name}
            </span>
            {document.status === "indexing" && (
              <div className="h-2 w-24 overflow-hidden rounded-full bg-muted">
                <div className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
              </div>
            )}
            <Badge variant={variant}>{label}</Badge>
          </li>
        );
      })}
    </ul>
  );
}
