import { forwardRef, useId } from "react";
import type { InputHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helperText?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, error, helperText, id, className, ...props },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const descriptionId = `${inputId}-description`;

  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label htmlFor={inputId} className="text-sm font-medium text-foreground">
          {label}
        </label>
      )}
      <input
        ref={ref}
        id={inputId}
        aria-invalid={error ? true : undefined}
        aria-describedby={error || helperText ? descriptionId : undefined}
        className={cn(
          "h-10 rounded-md border border-border bg-background px-3 text-sm text-foreground",
          "placeholder:text-muted-foreground",
          "focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring",
          "disabled:cursor-not-allowed disabled:opacity-50",
          error && "border-danger focus:border-danger focus:ring-danger/40",
          className,
        )}
        {...props}
      />
      {error ? (
        <p id={descriptionId} className="text-sm text-danger">
          {error}
        </p>
      ) : helperText ? (
        <p id={descriptionId} className="text-sm text-muted-foreground">
          {helperText}
        </p>
      ) : null}
    </div>
  );
});
