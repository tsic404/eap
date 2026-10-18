"use client";

import { useEffect } from "react";
import * as Sentry from "@sentry/nextjs";

/**
 * Root error boundary. Inline styles: when the root layout itself fails, its
 * global CSS may not have been emitted, so Tailwind classes are not guaranteed
 * to apply here. Colours stay slot references with the slot's current value as
 * fallback — a bare literal would drift from the token it restates (C-83).
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="zh-CN">
      <body
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          margin: 0,
          background: "var(--color-background, #ffffff)",
          color: "var(--color-foreground, #0f172a)",
        }}
      >
        <div style={{ textAlign: "center", padding: 24 }}>
          <h1 style={{ fontSize: 24, fontWeight: 700 }}>系统暂时不可用</h1>
          <p style={{ color: "var(--color-muted-foreground, #475569)" }}>
            发生了严重错误，请稍后重试。
          </p>
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: 16,
              padding: "8px 16px",
              borderRadius: 6,
              background: "var(--color-primary, #2563eb)",
              color: "var(--color-primary-foreground, #ffffff)",
              border: "none",
              cursor: "pointer",
            }}
          >
            重试
          </button>
        </div>
      </body>
    </html>
  );
}
