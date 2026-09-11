import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export type CardVariant = "default" | "hover" | "interactive";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: CardVariant;
}

const VARIANT_CLASSES: Record<CardVariant, string> = {
  default: "border border-border bg-background shadow-sm",
  hover: "border border-border bg-background shadow-sm transition-shadow hover:shadow-md",
  interactive:
    "cursor-pointer border border-border bg-background shadow-sm transition-all hover:border-primary hover:shadow-md",
};

export function Card({
  variant = "default",
  className,
  ...props
}: CardProps) {
  return (
    <div
      className={cn("rounded-lg", VARIANT_CLASSES[variant], className)}
      {...props}
    />
  );
}

export function CardHeader({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col gap-1 p-5", className)} {...props} />;
}

export function CardTitle({
  className,
  ...props
}: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn("text-base font-semibold text-foreground", className)}
      {...props}
    />
  );
}

export function CardDescription({
  className,
  ...props
}: HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p className={cn("text-sm text-muted-foreground", className)} {...props} />
  );
}

export function CardContent({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5 pt-0", className)} {...props} />;
}

export function CardFooter({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("flex items-center gap-2 p-5 pt-0", className)} {...props} />
  );
}
