"use client";

import { useMemo, useState } from "react";
import { ListTodo } from "lucide-react";
import { useSWRConfig } from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBanner } from "@/components/ui/error-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { extractApiErrorMessage } from "@/lib/platform-service";
import {
  TASK_PRIORITY_LABEL,
  TASK_STATUS_LABEL,
} from "@/lib/task-labels";
import {
  TASK_PRIORITIES,
  TASK_STATUSES,
  type TaskListParams,
} from "@/lib/task-types";
import { usePendingTaskCount, useTasks } from "@/lib/use-tasks";

import { TaskCard } from "./task-card";

interface FilterOption {
  value: string;
  label: string;
}

function buildFilterOptions(
  values: readonly string[],
  labelOf: (value: string) => string,
): FilterOption[] {
  return [
    { value: "", label: "全部" },
    ...values.map((value) => ({ value, label: labelOf(value) })),
  ];
}

function FilterGroup({
  title,
  options,
  value,
  onChange,
}: {
  title: string;
  options: FilterOption[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <h2 className="mb-1 text-sm font-semibold text-foreground">{title}</h2>
      {options.map((option) => (
        <Button
          key={option.value}
          variant={value === option.value ? "primary" : "ghost"}
          size="sm"
          className="justify-start"
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </Button>
      ))}
    </div>
  );
}

function TaskListSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      {Array.from({ length: 4 }, (_, index) => (
        <Skeleton key={index} className="h-28 w-full" />
      ))}
    </div>
  );
}

/** Task center: filter sidebar + cursor-paginated task card list (§14.3). */
export function TaskCenterPage() {
  const [statusFilter, setStatusFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");

  const params = useMemo<TaskListParams>(() => {
    const next: TaskListParams = {};
    if (statusFilter) next.status = statusFilter;
    if (priorityFilter) next.priority = priorityFilter;
    return next;
  }, [statusFilter, priorityFilter]);

  const {
    data: pages,
    error,
    isLoading,
    isValidating,
    size,
    setSize,
    mutate,
  } = useTasks(params);
  const { data: pending } = usePendingTaskCount();
  const { mutate: mutateGlobal } = useSWRConfig();

  // Revalidate every `tasks*` SWR key (list pages + pending count) after an action.
  const revalidateTasks = () => {
    void mutateGlobal(
      (key) => typeof key === "string" && key.startsWith("tasks"),
    );
  };

  const statusOptions = buildFilterOptions(
    TASK_STATUSES,
    (value) => TASK_STATUS_LABEL[value] ?? value,
  );
  const priorityOptions = buildFilterOptions(
    TASK_PRIORITIES,
    (value) => TASK_PRIORITY_LABEL[value] ?? value,
  );

  const tasks = useMemo(
    () => pages?.flatMap((page) => page.items) ?? [],
    [pages],
  );
  const lastPage = pages?.[pages.length - 1];
  const hasMore = lastPage?.next_cursor != null;

  return (
    <div className="flex flex-col gap-6 lg:flex-row">
      <aside className="flex flex-col gap-6 lg:w-56 lg:shrink-0">
        <FilterGroup
          title="状态"
          options={statusOptions}
          value={statusFilter}
          onChange={setStatusFilter}
        />
        <FilterGroup
          title="优先级"
          options={priorityOptions}
          value={priorityFilter}
          onChange={setPriorityFilter}
        />
      </aside>

      <section className="flex flex-1 flex-col gap-4">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-foreground">任务中心</h1>
          {pending != null && (
            <Badge variant={pending.count > 0 ? "warning" : "default"}>
              待审批 {pending.isExact ? pending.count : `${pending.count}+`}
            </Badge>
          )}
        </div>

        {isLoading ? (
          <TaskListSkeleton />
        ) : error ? (
          <ErrorBanner
            description={extractApiErrorMessage(error)}
            onRetry={() => mutate()}
          />
        ) : tasks.length === 0 ? (
          <EmptyState
            icon={<ListTodo className="h-6 w-6" />}
            title="暂无任务"
            description="尚无符合条件的任务"
          />
        ) : (
          <>
            <div className="flex flex-col gap-4">
              {tasks.map((task) => (
                <TaskCard key={task.id} task={task} onAction={revalidateTasks} />
              ))}
            </div>
            {hasMore && (
              <div className="flex justify-center">
                <Button
                  variant="outline"
                  onClick={() => setSize(size + 1)}
                  loading={isValidating}
                >
                  加载更多
                </Button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
