"use client";

import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

export interface DropdownItem {
  value: string;
  label: React.ReactNode;
  icon?: React.ReactNode;
  disabled?: boolean;
  danger?: boolean;
  onSelect?: () => void;
}

export interface DropdownProps {
  trigger: React.ReactNode;
  items: DropdownItem[];
  align?: "left" | "right";
  className?: string;
  triggerClassName?: string;
}
 
 /** Click-trigger dropdown menu. Closes on outside click, Escape, and select. */
export function Dropdown({
  trigger,
  items,
  align = "right",
  className,
  triggerClassName,
}: DropdownProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    // Move focus to the first enabled item so keyboard users can navigate.
    menuRef.current
      ?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')
      ?.focus();
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const handleMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const menuItems = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>(
        '[role="menuitem"]:not(:disabled)',
      ) ?? [],
    );
    if (menuItems.length === 0) return;

    const currentIndex = menuItems.indexOf(
      document.activeElement as HTMLButtonElement,
    );
    let nextIndex: number | null = null;
    if (event.key === "ArrowDown") {
      nextIndex = currentIndex < 0 ? 0 : (currentIndex + 1) % menuItems.length;
    } else if (event.key === "ArrowUp") {
      nextIndex =
        currentIndex < 0
          ? menuItems.length - 1
          : (currentIndex - 1 + menuItems.length) % menuItems.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = menuItems.length - 1;
    }
    if (nextIndex !== null) {
      event.preventDefault();
      menuItems[nextIndex].focus();
    }
  };

  return (
    <div ref={containerRef} className={cn("relative", className)}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={cn("inline-flex items-center", triggerClassName)}
      >
        {trigger}
      </button>
      {open && (
        <div
          ref={menuRef}
          role="menu"
          onKeyDown={handleMenuKeyDown}
          className={cn(
            "absolute z-40 mt-1 min-w-[10rem] overflow-hidden rounded-md border border-border bg-background p-1 shadow-md animate-scale-in",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          {items.map((item) => (
            <button
              key={item.value}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              onClick={() => {
                if (item.disabled) return;
                setOpen(false);
                item.onSelect?.();
              }}
              className={cn(
                "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm",
                item.danger
                  ? "text-danger hover:bg-danger-subtle"
                  : "text-foreground hover:bg-muted",
                item.disabled && "cursor-not-allowed opacity-50",
              )}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
