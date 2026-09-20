"use client";

import useSWR from "swr";
import useSWRInfinite from "swr/infinite";

import { listTasks } from "./task-service";
import type { TaskList, TaskListParams } from "./task-types";

const TASKS_KEY_PREFIX = "tasks";
const TASK_PAGE_SIZE = 20;
const PENDING_COUNT_LIMIT = 100;
const PENDING_POLL_INTERVAL_MS = 10_000;

function tasksKey(params: TaskListParams): string {
  return `${TASKS_KEY_PREFIX}:${JSON.stringify(params)}`;
}

function decodeTasksKey(key: string): TaskListParams {
  return JSON.parse(key.slice(TASKS_KEY_PREFIX.length + 1)) as TaskListParams;
}

/**
 * SWR-backed, cursor-paginated task list via `useSWRInfinite`. Each page's key
 * embeds the previous page's `next_cursor`, so `setSize(size + 1)` fetches and
 * appends the next page; the list stays reachable past the first 20 tasks.
 */
export function useTasks(params?: Omit<TaskListParams, "cursor">) {
  return useSWRInfinite<TaskList>(
    (pageIndex, previousPageData) => {
      if (previousPageData && previousPageData.next_cursor == null) return null;
      const pageParams: TaskListParams = { ...params };
      if (pageIndex > 0 && previousPageData?.next_cursor) {
        pageParams.cursor = previousPageData.next_cursor;
      }
      return tasksKey(pageParams);
    },
    (key) => listTasks({ ...decodeTasksKey(key), limit: TASK_PAGE_SIZE }),
  );
}

export interface PendingTaskCount {
  count: number;
  /** False when the backlog exceeds the polled page (count is a lower bound). */
  isExact: boolean;
}

/**
 * SWR-backed pending-approval count, polled every 10s (§31.2.5 P1). The figure
 * is the first page's item count; when `next_cursor` is set the true total is
 * higher, so `isExact` is false and the UI renders `100+` rather than a false
 * undercount.
 */
export function usePendingTaskCount() {
  return useSWR<PendingTaskCount>(
    `${TASKS_KEY_PREFIX}:pending-count`,
    async () => {
      const page = await listTasks({
        status: "pending",
        limit: PENDING_COUNT_LIMIT,
      });
      return { count: page.items.length, isExact: page.next_cursor == null };
    },
    { refreshInterval: PENDING_POLL_INTERVAL_MS },
  );
}
