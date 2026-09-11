"use client";

import { useState } from "react";

import { cn } from "@/lib/utils";

export interface TabItem {
  value: string;
  label: React.ReactNode;
  content?: React.ReactNode;
}

export interface TabsProps {
  items: TabItem[];
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
  className?: string;
}

/** Horizontal tab switcher. Controlled via `value`/`onChange`, or uncontrolled. */
export function Tabs({
  items,
  value,
  defaultValue,
  onChange,
  className,
}: TabsProps) {
  const [internalValue, setInternalValue] = useState(
    defaultValue ?? items[0]?.value,
  );
  const isControlled = value !== undefined;
  const activeValue = isControlled ? value : internalValue;

  const select = (nextValue: string) => {
    if (!isControlled) setInternalValue(nextValue);
    onChange?.(nextValue);
  };

  const activeItem = items.find((item) => item.value === activeValue);

  return (
    <div className={cn("flex flex-col gap-4", className)}>
      <div
        role="tablist"
        className="flex items-center gap-1 border-b border-border"
      >
        {items.map((item) => {
          const isActive = item.value === activeValue;
          return (
            <button
              key={item.value}
              role="tab"
              aria-selected={isActive}
              onClick={() => select(item.value)}
              className={cn(
                "border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      <div role="tabpanel">{activeItem?.content}</div>
    </div>
  );
}
