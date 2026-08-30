"use client";

import type { Session } from "@supabase/supabase-js";
import { useEffect, useMemo, useState } from "react";

import { getSupabaseBrowserClient } from "../lib/supabase";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type ExportFormat = "markdown" | "json";

export function ReportExportButtons({ jobId }: { jobId: string }) {
  const supabase = useMemo(() => getSupabaseBrowserClient(), []);
  const [session, setSession] = useState<Session | null>(null);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!supabase) return;
    let active = true;
    void supabase.auth.getSession().then(({ data }) => {
      if (active) setSession(data.session);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
    };
  }, [supabase]);

  async function download(format: ExportFormat) {
    if (!session) {
      setMessage("Sign in to export this private report.");
      return;
    }
    setExporting(format);
    setMessage(null);
    try {
      const response = await fetch(
        `${API_BASE}/v1/research/jobs/${encodeURIComponent(jobId)}/export?format=${format}`,
        { headers: { Authorization: `Bearer ${session.access_token}` } },
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body?.detail ?? `Export failed with status ${response.status}`);
      }
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition");
      const fallback = `india-equity-research-${jobId}.${format === "markdown" ? "md" : "json"}`;
      const filename = disposition?.match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.style.display = "none";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to export report");
    } finally {
      setExporting(null);
    }
  }

  return (
    <div className="exportControls" aria-label="Report exports">
      <button
        type="button"
        className="secondaryButton"
        disabled={Boolean(exporting)}
        onClick={() => void download("markdown")}
      >
        {exporting === "markdown" ? "Preparing…" : "Download Markdown"}
      </button>
      <button
        type="button"
        className="secondaryButton"
        disabled={Boolean(exporting)}
        onClick={() => void download("json")}
      >
        {exporting === "json" ? "Preparing…" : "Download JSON"}
      </button>
      {message ? <small className="exportMessage">{message}</small> : null}
    </div>
  );
}
