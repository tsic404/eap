"use client";

import { Inbox } from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

import { Card } from "./card";

/** `plain` sits inline in a page section; `card` is a standalone guidance surface. */
export type EmptyStateVariant = "plain" | "card";

export interface EmptyStateProps {
  title?: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  variant?: EmptyStateVariant;
  className?: string;
}

/**
 * Placeholder for empty lists/results, with optional call-to-action.
 * The `card` variant frames the placeholder as a guidance card that carries the
 * next step, so an empty admin list never renders as a blank area.
 */
export function EmptyState({
  title,
  description,
  icon,
  action,
  variant = "plain",
  className,
}: EmptyStateProps) {
  const t = useTranslations("empty");
  const resolvedTitle = title ?? t("default");

  const content = (
    <>
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
        {icon ?? <Inbox className="h-6 w-6" />}
      </div>
      <div className="flex flex-col gap-1">
        <p className="text-sm font-medium text-foreground">{resolvedTitle}</p>
        {description && (
          <p className="text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {action}
    </>
  );

  if (variant === "card") {
    return (
      <Card
        role="region"
        aria-label={resolvedTitle}
        className={cn(
          "flex flex-col items-center justify-center gap-4 px-6 py-16 text-center",
          className,
        )}
      >
        {content}
      </Card>
    );
  }

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 px-6 py-12 text-center",
        className,
      )}
    >
      {content}
    </div>
  );
}
