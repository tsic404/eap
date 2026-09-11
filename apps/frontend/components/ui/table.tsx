"use client";

import { useMemo, useState } from "react";
import {
  ArrowUpDown,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
} from "lucide-react";

import { cn } from "@/lib/utils";

import { Button } from "./button";
import { EmptyState } from "./empty-state";

export interface Column<T> {
  key: string;
  header: React.ReactNode;
  sortable?: boolean;
  sortValue?: (row: T) => string | number;
  cell?: (row: T) => React.ReactNode;
  className?: string;
}

export type SortDirection = "asc" | "desc";

export interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  rowKey: (row: T) => string;
  selectable?: boolean;
  selectedKeys?: string[];
  onSelectionChange?: (keys: string[]) => void;
  pageSize?: number;
  emptyTitle?: string;
  emptyDescription?: string;
}

function defaultValue<T>(row: T, key: string): string {
  const value = (row as unknown as Record<string, unknown>)[key];
  return value == null ? "" : String(value);
}

/** Generic data table with sorting, pagination, row selection, and empty state. */
export function DataTable<T>({
  columns,
  data,
  rowKey,
  selectable = false,
  selectedKeys,
  onSelectionChange,
  pageSize = 10,
  emptyTitle,
  emptyDescription,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<{
    key: string;
    direction: SortDirection;
  } | null>(null);
  const [page, setPage] = useState(0);
  const [internalSelected, setInternalSelected] = useState<string[]>([]);

  const isControlled = selectedKeys !== undefined;
  const selected = isControlled ? selectedKeys : internalSelected;

  const sortedData = useMemo(() => {
    if (!sort) return data;
    const column = columns.find((item) => item.key === sort.key);
    const sortValue = column?.sortValue;
    if (!sortValue) return data;
    const direction = sort.direction === "asc" ? 1 : -1;
    return [...data].sort((a, b) => {
      const av = sortValue(a);
      const bv = sortValue(b);
      if (av < bv) return -direction;
      if (av > bv) return direction;
      return 0;
    });
  }, [data, sort, columns]);

  const normalizedPageSize =
    Number.isInteger(pageSize) && pageSize > 0 ? pageSize : 10;
  const pageCount = Math.max(1, Math.ceil(sortedData.length / normalizedPageSize));
  const currentPage = Math.min(page, pageCount - 1);
  const pageData = sortedData.slice(
    currentPage * normalizedPageSize,
    (currentPage + 1) * normalizedPageSize,
  );
  const pageKeys = pageData.map(rowKey);

  const setSelected = (next: string[]) => {
    if (!isControlled) setInternalSelected(next);
    onSelectionChange?.(next);
  };

  const toggleSort = (column: Column<T>) => {
    if (!column.sortable) return;
    setPage(0);
    setSort((prev) => {
      if (prev?.key === column.key) {
        return prev.direction === "asc"
          ? { key: column.key, direction: "desc" }
          : null;
      }
      return { key: column.key, direction: "asc" };
    });
  };

  const toggleRow = (key: string) => {
    setSelected(
      selected.includes(key)
        ? selected.filter((item) => item !== key)
        : [...selected, key],
    );
  };

  const toggleAll = () => {
    const allSelected =
      pageKeys.length > 0 && pageKeys.every((key) => selected.includes(key));
    setSelected(
      allSelected
        ? selected.filter((key) => !pageKeys.includes(key))
        : [...new Set([...selected, ...pageKeys])],
    );
  };

  if (data.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />;
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="bg-muted">
            <tr>
              {selectable && (
                <th className="w-10 px-3 py-2.5">
                  <input
                    type="checkbox"
                    aria-label="选择当前页全部"
                    checked={
                      pageKeys.length > 0 &&
                      pageKeys.every((key) => selected.includes(key))
                    }
                    onChange={toggleAll}
                  />
                </th>
              )}
              {columns.map((column) => (
                <th
                  key={column.key}
                  className={cn(
                    "px-3 py-2.5 font-medium text-foreground",
                    column.className,
                  )}
                >
                  {column.sortable ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(column)}
                      className="inline-flex items-center gap-1"
                    >
                      {column.header}
                      {sort?.key === column.key ? (
                        sort.direction === "asc" ? (
                          <ChevronUp className="h-4 w-4" />
                        ) : (
                          <ChevronDown className="h-4 w-4" />
                        )
                      ) : (
                        <ArrowUpDown className="h-4 w-4 text-muted-foreground" />
                      )}
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageData.map((row) => {
              const key = rowKey(row);
              return (
                <tr key={key} className="border-t border-border">
                  {selectable && (
                    <td className="px-3 py-2.5">
                      <input
                        type="checkbox"
                        aria-label="选择行"
                        checked={selected.includes(key)}
                        onChange={() => toggleRow(key)}
                      />
                    </td>
                  )}
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      className={cn(
                        "px-3 py-2.5 text-foreground",
                        column.className,
                      )}
                    >
                      {column.cell
                        ? column.cell(row)
                        : defaultValue(row, column.key)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>
          第 {currentPage + 1} / {pageCount} 页
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={currentPage === 0}
            onClick={() => setPage(currentPage - 1)}
          >
            <ChevronLeft className="h-4 w-4" />
            上一页
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={currentPage >= pageCount - 1}
            onClick={() => setPage(currentPage + 1)}
          >
            下一页
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
