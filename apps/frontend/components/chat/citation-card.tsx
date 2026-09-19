"use client";

import { FileText } from "lucide-react";

import type { Citation } from "@/lib/conversation-types";
import { cn } from "@/lib/utils";

const EXTENSION_COLORS: Record<string, string> = {
  pdf: "text-danger",
  doc: "text-info",
  docx: "text-info",
  xls: "text-success",
  xlsx: "text-success",
};

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot + 1).toLowerCase();
}

function scoreLabel(score: number | null | undefined): string {
  if (score === null || score === undefined) return "";
  return `${Math.round(score * 100)}%`;
}

function scoreColor(score: number | null | undefined): string {
  if (score === null || score === undefined) return "text-muted-foreground";
  if (score >= 0.7) return "text-success";
  if (score >= 0.4) return "text-warning";
  return "text-muted-foreground";
}

/** Horizontal citation card: file icon, source, knowledge base, excerpt, score. */
export function CitationCard({ citation }: { citation: Citation }) {
  const iconColor = EXTENSION_COLORS[extensionOf(citation.sourceName)] ?? "text-muted-foreground";

  return (
    <div className="flex items-start gap-3 rounded-md border border-border bg-background p-3">
      <FileText className={cn("mt-0.5 h-5 w-5 shrink-0", iconColor)} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-foreground">
          {citation.sourceName}
        </p>
        {citation.kbName && (
          <p className="text-xs text-muted-foreground">{citation.kbName}</p>
        )}
        {citation.excerpt && (
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
            {citation.excerpt}
          </p>
        )}
      </div>
      <span className={cn("shrink-0 text-xs font-medium", scoreColor(citation.score))}>
        {scoreLabel(citation.score)}
      </span>
    </div>
  );
}
