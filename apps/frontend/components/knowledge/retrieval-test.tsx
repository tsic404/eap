"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { useToast } from "@/components/ui/toast";
import { retrievalTest } from "@/lib/knowledge-service";
import type { RetrievalTestResult } from "@/lib/knowledge-types";
import { extractApiErrorMessage } from "@/lib/platform-service";

import { ChunkList } from "./chunk-list";

const MIN_TOP_K = 1;
const MAX_TOP_K = 10;
const DEFAULT_TOP_K = 3;

export interface RetrievalTestProps {
  kbId: string;
  disabled?: boolean;
}

/** Retrieval test form: query + Top K slider → recalled-chunk cards. */
export function RetrievalTest({ kbId, disabled = false }: RetrievalTestProps) {
  const { toast } = useToast();
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(DEFAULT_TOP_K);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RetrievalTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    const trimmedQuery = query.trim();
    if (trimmedQuery === "") {
      toast({ type: "error", title: "请输入查询内容" });
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const data = await retrievalTest(kbId, {
        query: trimmedQuery,
        retrieval_model: { top_k: topK },
      });
      setResult(data);
    } catch (runError) {
      setError(extractApiErrorMessage(runError));
      setResult(null);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="retrieval-query"
          className="text-sm font-medium text-foreground"
        >
          查询内容
        </label>
        <textarea
          id="retrieval-query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          disabled={disabled}
          rows={3}
          placeholder="输入查询，测试知识库召回效果"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="retrieval-top-k"
          className="text-sm font-medium text-foreground"
        >
          Top K：{topK}
        </label>
        <input
          id="retrieval-top-k"
          type="range"
          min={MIN_TOP_K}
          max={MAX_TOP_K}
          value={topK}
          onChange={(event) => setTopK(Number(event.target.value))}
          disabled={disabled}
          className="w-full accent-primary disabled:cursor-not-allowed disabled:opacity-50"
        />
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>{MIN_TOP_K}</span>
          <span>{MAX_TOP_K}</span>
        </div>
      </div>

      <Button
        onClick={run}
        loading={running}
        disabled={disabled}
        className="self-start"
      >
        运行测试
      </Button>

      {error && <ErrorBanner description={error} />}

      {result && (
        <ChunkList
          citations={result.citations}
          query={result.query}
          score={result.score}
          latencyMs={result.latencyMs}
        />
      )}
    </div>
  );
}
