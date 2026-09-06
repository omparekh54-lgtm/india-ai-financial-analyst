"use client";

import type { Session } from "@supabase/supabase-js";
import { useEffect, useMemo, useState } from "react";

import { getSupabaseBrowserClient } from "../lib/supabase";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type AgentReadinessItem = {
  agent: string;
  ready: boolean;
  errors: string[];
  warnings: string[];
};

type DataReadinessResponse = {
  ready: boolean;
  errors: string[];
  warnings: string[];
  blocking_agents?: string[];
  agent_readiness?: {
    ready: boolean;
    blocking_agents: string[];
    agents: AgentReadinessItem[];
  };
  coverage: {
    market_bars: number;
    latest_market_bar?: string | null;
  };
};

export function BrokerConnectionBar() {
  const supabase = useMemo(() => getSupabaseBrowserClient(), []);
  const [session, setSession] = useState<Session | null>(null);
  const [dataReadiness, setDataReadiness] = useState<DataReadinessResponse | null>(null);

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
      setDataReadiness(null);
      return;
    }
    let active = true;
    void loadDataReadiness(session.access_token)
      .then((payload) => {
        if (active) setDataReadiness(payload);
      })
      .catch(() => {
        if (active) setDataReadiness(null);
      });
    return () => {
      active = false;
    };
  }, [session]);

  if (!session) return null;

  const corpusComplete = Boolean(dataReadiness?.ready && dataReadiness.warnings.length === 0);
  const blockingAgents =
    dataReadiness?.blocking_agents ?? dataReadiness?.agent_readiness?.blocking_agents ?? [];
  const blockedAgentDetail = blockingAgents.length
    ? `Blocked agents: ${blockingAgents.slice(0, 3).map(prettyAgentName).join(", ")}${blockingAgents.length > 3 ? ` +${blockingAgents.length - 3}` : ""}`
    : null;
  const corpusDetail = dataReadiness
    ? dataReadiness.ready
      ? dataReadiness.warnings[0] ?? "All agent data and freshness checks passed"
      : blockedAgentDetail ?? dataReadiness.errors[0] ?? "Production data bootstrap is required"
    : "Checking security universe, evidence and agent-level data coverage…";
  const latestBar = dataReadiness?.coverage.latest_market_bar;
  const latestBarLabel = latestBar ? formatDate(latestBar) : "waiting for first completed import";

  return (
    <aside className="brokerBar" aria-label="Research and end-of-day market data status">
      <div className="brokerBarInner">
        <div className="brokerIdentity">
          <span className={latestBar ? "statusDot" : "statusDot warningDot"} />
          <div>
            <strong>End-of-day market history</strong>
            <small>Latest completed session: {latestBarLabel} · never labeled live/intraday</small>
          </div>
        </div>
        <div className="brokerIdentity">
          <span className={corpusComplete ? "statusDot" : "statusDot warningDot"} />
          <div>
            <strong>Research corpus</strong>
            <small>{corpusDetail}</small>
          </div>
        </div>
        <div className="brokerControls">
          <span className="safeBadge">EOD · DELAYED</span>
          <span className={corpusComplete ? "liveBadge" : "safeBadge"}>
            {dataReadiness
              ? `${dataReadiness.coverage.market_bars.toLocaleString()} DAILY BARS`
              : "CHECKING"}
          </span>
        </div>
      </div>
    </aside>
  );
}

async function loadDataReadiness(accessToken: string): Promise<DataReadinessResponse> {
  const response = await fetch(`${API_BASE}/v1/system/data-readiness`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body?.detail ?? "Unable to load research data readiness");
  }
  return body as DataReadinessResponse;
}

function prettyAgentName(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDate(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  });
}
