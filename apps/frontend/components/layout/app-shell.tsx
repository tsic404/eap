"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

import { Header } from "./header";

export interface AppShellProps {
  /** Navigation sidebar element; responsive collapse is its own CSS concern. */
  sidebar: React.ReactNode;
  children: React.ReactNode;
}

/**
 * Responsive workspace shell (§30.10): full sidebar on desktop, icon-only on
 * tablet, and a hamburger-triggered drawer on mobile.
 */
export function AppShell({ sidebar, children }: AppShellProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  // Lock body scroll and move focus into the drawer while it is open.
  useEffect(() => {
    if (!drawerOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [drawerOpen]);

  // Escape closes; Tab cycles within the drawer (focus trap).
  useEffect(() => {
    if (!drawerOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setDrawerOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const drawer = drawerRef.current;
      if (!drawer) return;
      const focusables = drawer.querySelectorAll<HTMLElement>(
        'button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen]);

  return (
    <div className="flex min-h-screen flex-col">
      <Header onMenuToggle={() => setDrawerOpen(true)} />
      <div className="flex flex-1">
        <aside className="hidden shrink-0 border-r border-border md:block">
          {sidebar}
        </aside>
        <div className="min-w-0 flex-1">{children}</div>
      </div>

      {drawerOpen && (
        <div
          ref={drawerRef}
          role="dialog"
          aria-modal="true"
          aria-label="导航菜单"
          className="fixed inset-0 z-50 md:hidden"
        >
          <button
            type="button"
            aria-label="关闭导航"
            className="absolute inset-0 bg-black/40"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="relative flex h-full w-64 flex-col bg-background shadow-lg">
            <div className="flex justify-end p-2">
              <button
                ref={closeButtonRef}
                type="button"
                aria-label="关闭导航"
                onClick={() => setDrawerOpen(false)}
                className="rounded-md p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">{sidebar}</div>
          </div>
        </div>
      )}
    </div>
  );
}
