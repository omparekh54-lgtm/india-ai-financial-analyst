"use client";

import type { Session } from "@supabase/supabase-js";
import { useEffect, useMemo, useState } from "react";

import { getSupabaseBrowserClient } from "../lib/supabase";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type AgentReadiness = {
  agent: string;
  ready: boolean;
  errors?: string[];
  warnings?: string[];
};

type ReadinessPayload = {
  ready: boolean;
  blocking_agents?: string[];
  agent_readiness?: {
    ready: boolean;
    blocking_agents?: string[];
    agents?: AgentReadiness[];
  };
};

export function SystemReadinessBar() {
  const supabase = useMemo(() => getSupabaseBrowserClient(), []);
  const [session, setSession] = useState<Session | null>(null);
  const [payload, setPayload] = useState<ReadinessPayload | null>(null);

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
    if (!session) {
      setPayload(null);
      return;
    }
    let active = true;
    void fetch(`${API_BASE}/v1/system/data-readiness`, {
      headers: { Authorization: `Bearer ${session.access_token}` },
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Readiness API returned ${response.status}`);
        return response.json() as Promise<ReadinessPayload>;
      })
      .then((body) => {
        if (active) setPayload(body);
      })
      .catch(() => {
        if (active) setPayload(null);
      });
    return () => {
      active = false;
    };
  }, [session]);

  if (!session || !payload) return null;

  const agents = payload.agent_readiness?.agents ?? [];
  const readyCount = agents.filter((agent) => agent.ready).length;
  const total = agents.length || 16;
  const blocking = payload.agent_readiness?.blocking_agents ?? payload.blocking_agents ?? [];

  return (
    <aside className={payload.ready ? "readinessBar readinessGreen" : "readinessBar readinessAmber"}>
      <span className="readinessDot" />
      <strong>{payload.ready ? "Research corpus ready" : "Production corpus not ready"}</strong>
      <span>{readyCount}/{total} roles currently data-ready</span>
      {blocking.length ? (
        <details>
          <summary>Blocking roles</summary>
          <span>{blocking.map(humanize).join(" · ")}</span>
        </details>
      ) : null}
    </aside>
  );
}

function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
