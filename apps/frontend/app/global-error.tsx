"use client";

import { useEffect } from "react";
import * as Sentry from "@sentry/nextjs";

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

  // Inline styles: when the root layout itself fails, its global CSS may not
  // have been emitted, so Tailwind classes are not guaranteed to apply here.
  return (
    <html lang="zh-CN">
      <body
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          margin: 0,
        }}
      >
        <div style={{ textAlign: "center", padding: 24 }}>
          <h1 style={{ fontSize: 24, fontWeight: 700 }}>系统暂时不可用</h1>
          <p style={{ color: "#64748b" }}>发生了严重错误，请稍后重试。</p>
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: 16,
              padding: "8px 16px",
              borderRadius: 6,
              background: "#2563eb",
              color: "#ffffff",
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
