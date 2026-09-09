"use client";

import { useEffect, useState } from "react";

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
  const [payload, setPayload] = useState<ReadinessPayload | null>(null);

  useEffect(() => {
    let active = true;
    void fetch(`${API_BASE}/v1/system/data-readiness`)
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
  }, []);

  if (!payload) return null;

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
