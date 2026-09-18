"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { KnowledgeBase } from "@/lib/knowledge-types";

import { INDEXING_STATUS_LABEL, INDEXING_STATUS_VARIANT } from "./kb-labels";

export interface KBCardProps {
  knowledgeBase: KnowledgeBase;
  onClick?: () => void;
}

/** Knowledge-base card: name + indexing status + description + doc/chunk counts. */
export function KBCard({ knowledgeBase, onClick }: KBCardProps) {
  const status = knowledgeBase.indexing_status ?? "ready";
  const statusLabel = INDEXING_STATUS_LABEL[status] ?? status;
  const statusVariant = INDEXING_STATUS_VARIANT[status] ?? "default";

  return (
    <Card
      variant={onClick ? "interactive" : "default"}
      onClick={onClick}
      className="flex h-full flex-col"
    >
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-muted text-2xl leading-none">
              📚
            </span>
            <div className="flex min-w-0 flex-col">
              <CardTitle className="truncate">{knowledgeBase.name}</CardTitle>
              <span className="text-xs text-muted-foreground">
                {knowledgeBase.kb_id}
              </span>
            </div>
          </div>
          <Badge variant={statusVariant}>{statusLabel}</Badge>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col justify-between gap-3">
        <p className="line-clamp-2 text-sm text-muted-foreground">
          {knowledgeBase.description ?? "暂无描述"}
        </p>
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          <span>文档 {knowledgeBase.doc_count}</span>
          <span>分块 {knowledgeBase.chunk_count}</span>
        </div>
      </CardContent>
    </Card>
  );
}
