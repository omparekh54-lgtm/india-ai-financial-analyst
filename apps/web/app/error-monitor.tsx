"use client";

import { useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export function ErrorMonitor() {
  useEffect(() => {
    let sentCount = 0;
    const report = (kind: string, message: string, stack?: string) => {
      if (sentCount >= 5) return;
      sentCount += 1;
      const pagePath = ["/", "/watchlists"].includes(window.location.pathname)
        ? window.location.pathname : "/other";
      void fetch(`${API_BASE}/v1/system/browser-errors`, {
        method: "POST",
        keepalive: true,
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          kind,
          message: `Frontend ${kind}`,
          page_path: pagePath.slice(0, 500),
        }),
      }).catch(() => undefined);
    };
    const onError = (event: ErrorEvent) => {
      report("window_error", event.message || "Unhandled browser error", event.error?.stack);
    };
    const onUnhandledRejection = (event: PromiseRejectionEvent) => {
      const reason = event.reason;
      report(
        "unhandled_rejection",
        reason instanceof Error ? reason.message : String(reason ?? "Unhandled promise rejection"),
        reason instanceof Error ? reason.stack : undefined,
      );
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onUnhandledRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
    };
  }, []);

  return null;
}
