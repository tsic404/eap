import { Loader2, type LucideProps } from "lucide-react";

import { cn } from "@/lib/utils";

export type SpinnerSize = "sm" | "md" | "lg";

export interface SpinnerProps extends LucideProps {
  size?: SpinnerSize;
}

const SIZE_CLASSES: Record<SpinnerSize, string> = {
  sm: "h-4 w-4",
  md: "h-5 w-5",
  lg: "h-8 w-8",
};

/** Indeterminate loading indicator; inherits text color via `text-current`. */
export function Spinner({ size = "md", className, ...props }: SpinnerProps) {
  return (
    <Loader2
      role="status"
      aria-label="加载中"
      className={cn("animate-spin text-current", SIZE_CLASSES[size], className)}
      {...props}
    />
  );
}
