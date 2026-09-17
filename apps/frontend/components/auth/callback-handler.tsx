"use client";

import { Spinner } from "@/components/ui/spinner";

/** Full-screen loading state shown while the OIDC callback resolves. */
export function CallbackHandler() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3">
      <Spinner size="lg" className="text-primary" />
      <p className="text-sm text-muted-foreground">正在完成登录…</p>
    </div>
  );
}
