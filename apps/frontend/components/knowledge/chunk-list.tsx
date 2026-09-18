"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Modal } from "@/components/ui/modal";
import type { RetrievalCitation } from "@/lib/knowledge-types";

function Highlight({ text, query }: { text: string; query: string }) {
  if (query === "") return <>{text}</>;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${escaped})`, "gi"));
  return (
    <>
      {parts.map((part, index) =>
        part.toLowerCase() === query.toLowerCase() ? (
          <mark
            key={index}
            className="rounded bg-warning-subtle px-0.5 text-inherit"
          >
            {part}
          </mark>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
}

function scoreVariant(score: number): "success" | "warning" | "default" {
  if (score >= 0.8) return "success";
  if (score >= 0.5) return "warning";
  return "default";
}

export interface ChunkListProps {
  citations: RetrievalCitation[];
  query: string;
  score: number;
  latencyMs: number;
}

/** Recalled-chunk cards (score + excerpt + highlight) with a detail modal. */
export function ChunkList({
  citations,
  query,
  score,
  latencyMs,
}: ChunkListProps) {
  const [selected, setSelected] = useState<RetrievalCitation | null>(null);

  if (citations.length === 0) {
    return (
      <EmptyState
        title="未召回相关内容"
        description="尝试调整查询内容或增大 Top K"
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        <span>最高分 {score.toFixed(3)}</span>
        <span>耗时 {latencyMs.toFixed(0)} ms</span>
        <span>命中 {citations.length} 条</span>
      </div>

      <div className="flex flex-col gap-3">
        {citations.map((citation, index) => (
          <Card
            key={`${citation.source_name ?? ""}-${index}`}
            variant="interactive"
            onClick={() => setSelected(citation)}
            className="flex flex-col gap-2"
          >
            <div className="flex items-center justify-between">
              <Badge variant={scoreVariant(citation.score)}>
                分数 {citation.score.toFixed(3)}
              </Badge>
              {citation.source_name && (
                <span className="truncate text-xs text-muted-foreground">
                  {citation.source_name}
                </span>
              )}
            </div>
            <CardContent className="p-0">
              <p className="line-clamp-3 text-sm text-muted-foreground">
                <Highlight text={citation.content} query={query} />
              </p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Modal
        open={selected !== null}
        onClose={() => setSelected(null)}
        title="分块详情"
        className="max-w-2xl"
      >
        {selected && (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-3">
              <Badge variant={scoreVariant(selected.score)}>
                分数 {selected.score.toFixed(3)}
              </Badge>
              {selected.source_name && (
                <span className="text-sm text-muted-foreground">
                  {selected.source_name}
                </span>
              )}
            </div>
            <p className="whitespace-pre-wrap text-sm text-foreground">
              {selected.content}
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}
