"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { ROUTES } from "@/lib/api-routes";
import { extractApiErrorMessage } from "@/lib/platform-service";
import { listRunLogs } from "@/lib/run-log-service";
import {
  EMPTY_RUN_LOG_FILTERS,
  type RunLogFilters,
  type RunLogListParams,
  type RunLogSummary,
} from "@/lib/run-log-types";
import { useAllAgents } from "@/lib/use-agents";

import { LogFilterBar } from "./log-filter-bar";
import { RunLogTable } from "./run-log-table";

const PAGE_SIZE = 20;

function toListParams(filters: RunLogFilters, cursor?: string): RunLogListParams {
  const params: RunLogListParams = { limit: PAGE_SIZE };
  if (filters.agentId) params.agentId = filters.agentId;
  if (filters.conversationId) params.conversationId = filters.conversationId;
  if (filters.status) params.status = filters.status;
  if (filters.from) params.from = filters.from;
  // `to` is a date-only picker value; append end-of-day so a datetime parse
  // includes the whole selected day instead of its midnight boundary.
  if (filters.to) params.to = `${filters.to}T23:59:59`;
  if (cursor) params.cursor = cursor;
  return params;
}

/** Admin run-log listing: filter bar + cursor-paginated table. */
export function RunLogListPage() {
  const router = useRouter();
  const { data: agents } = useAllAgents();

  const [filters, setFilters] = useState<RunLogFilters>(EMPTY_RUN_LOG_FILTERS);
  const [reloadKey, setReloadKey] = useState(0);
  const [items, setItems] = useState<RunLogSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [loadMoreError, setLoadMoreError] = useState<unknown>(null);
  // Monotonic request generation: bumped on every first-page fetch so stale
  // load-more responses from a previous filter are dropped before merging.
  const requestSeqRef = useRef(0);

  useEffect(() => {
    const seq = ++requestSeqRef.current;
    let cancelled = false;
    setIsLoading(true);
    setLoadingMore(false);
    setError(null);
    setLoadMoreError(null);
    listRunLogs(toListParams(filters))
      .then((response) => {
        if (cancelled || seq !== requestSeqRef.current) return;
        setItems(response.items);
        setNextCursor(response.nextCursor);
      })
      .catch((err: unknown) => {
        if (!cancelled && seq === requestSeqRef.current) setError(err);
      })
      .finally(() => {
        if (!cancelled && seq === requestSeqRef.current) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filters, reloadKey]);

  const loadMore = async () => {
    if (loadingMore || !nextCursor) return;
    const seq = requestSeqRef.current;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const response = await listRunLogs(toListParams(filters, nextCursor));
      if (seq !== requestSeqRef.current) return;
      setItems((prev) => [...prev, ...response.items]);
      setNextCursor(response.nextCursor);
    } catch (err) {
      if (seq === requestSeqRef.current) setLoadMoreError(err);
    } finally {
      if (seq === requestSeqRef.current) setLoadingMore(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-8">
      <div>
        <h1 className="text-2xl font-bold text-foreground">运行日志</h1>
        <p className="text-sm text-muted-foreground">
          对话与工具调用的执行轨迹审计
        </p>
      </div>

      <LogFilterBar
        agents={agents ?? []}
        value={filters}
        onChange={setFilters}
      />

      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : error ? (
        <ErrorBanner
          description={extractApiErrorMessage(error)}
          onRetry={() => setReloadKey((key) => key + 1)}
        />
      ) : (
        <>
          <RunLogTable
            items={items}
            onSelect={(traceId) =>
              router.push(ROUTES.adminRunLogDetail(traceId))
            }
          />
          {nextCursor && (
            <div className="flex flex-col items-center gap-2">
              {loadMoreError ? (
                <p className="text-sm text-danger">
                  {extractApiErrorMessage(loadMoreError)}
                </p>
              ) : null}
              <Button variant="outline" onClick={loadMore} loading={loadingMore}>
                加载更多
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
