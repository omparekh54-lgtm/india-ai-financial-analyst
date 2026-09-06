"use client";

import type { Session } from "@supabase/supabase-js";
import { useEffect, useMemo, useState } from "react";

import { getSupabaseBrowserClient } from "../lib/supabase";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export function ErrorMonitor() {
  const supabase = useMemo(() => getSupabaseBrowserClient(), []);
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    if (!supabase) return;
    let active = true;
    void supabase.auth.getSession().then(({ data }) => {
      if (active) setSession(data.session);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      if (active) setSession(nextSession);
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
    };
  }, [supabase]);

  useEffect(() => {
    if (!session) return;
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
          Authorization: `Bearer ${session.access_token}`,
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
  }, [session]);

  return null;
}
