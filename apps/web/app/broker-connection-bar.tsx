"use client";

import type { Session } from "@supabase/supabase-js";
import { useEffect, useMemo, useState } from "react";

import { getSupabaseBrowserClient } from "../lib/supabase";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type BrokerConnection = {
  provider: string;
  connected: boolean;
  status: string;
  token_expires_at?: string | null;
  provider_user_id?: string | null;
  provider_user_name?: string | null;
  updated_at?: string | null;
};

type BrokerStatusResponse = {
  live_market_enabled: boolean;
  connections: BrokerConnection[];
};

export function BrokerConnectionBar() {
  const supabase = useMemo(() => getSupabaseBrowserClient(), []);
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<BrokerStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
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

  useEffect(() => {
    if (!session) {
      setStatus(null);
      return;
    }
    void loadBrokerStatus(session.access_token)
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [session]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("broker") !== "upstox") return;
    const callbackStatus = params.get("status");
    if (callbackStatus === "connected") {
      setMessage("Upstox connected securely. Fresh market snapshots can be used when live market is enabled.");
    } else if (callbackStatus === "error") {
      setMessage("Upstox connection was not completed. You can retry the secure login flow.");
    }
    params.delete("broker");
    params.delete("status");
    const query = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
  }, []);

  useEffect(() => {
    if (!session || !message?.startsWith("Upstox connected")) return;
    void loadBrokerStatus(session.access_token)
      .then(setStatus)
      .catch(() => undefined);
  }, [message, session]);

  if (!session) return null;

  const upstox = status?.connections.find((item) => item.provider === "upstox") ?? null;
  const connected = Boolean(upstox?.connected);

  async function connect() {
    if (!session) return;
    setLoading(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/v1/brokers/upstox/connect`, {
        method: "POST",
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      const body = await response.json();
      if (!response.ok || typeof body?.authorize_url !== "string") {
        throw new Error(body?.detail ?? "Unable to start Upstox connection");
      }
      window.location.assign(body.authorize_url);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to connect Upstox");
      setLoading(false);
    }
  }

  async function disconnect() {
    if (!session) return;
    setLoading(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/v1/brokers/upstox`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body?.detail ?? "Unable to disconnect Upstox");
      }
      setStatus(await loadBrokerStatus(session.access_token));
      setMessage("Upstox disconnected. Stored market data remains available as the delayed fallback.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to disconnect Upstox");
    } finally {
      setLoading(false);
    }
  }

  return (
    <aside className="brokerBar" aria-label="Live market connection">
      <div className="brokerBarInner">
        <div className="brokerIdentity">
          <span className={connected ? "statusDot" : "statusDot warningDot"} />
          <div>
            <strong>Upstox market data</strong>
            <small>
              {connected
                ? `Connected${upstox?.token_expires_at ? ` · token expires ${formatDate(upstox.token_expires_at)}` : ""}`
                : "Not connected · research uses delayed stored market data"}
            </small>
          </div>
        </div>
        <div className="brokerControls">
          <span className={status?.live_market_enabled ? "liveBadge" : "safeBadge"}>
            {status?.live_market_enabled ? "LIVE OVERLAY ON" : "LIVE OVERLAY OFF"}
          </span>
          <button
            type="button"
            className="secondaryButton"
            disabled={loading}
            onClick={() => void (connected ? disconnect() : connect())}
          >
            {loading ? "Please wait…" : connected ? "Disconnect" : "Connect Upstox"}
          </button>
        </div>
      </div>
      {message ? <div className="brokerMessage">{message}</div> : null}
    </aside>
  );
}

async function loadBrokerStatus(accessToken: string): Promise<BrokerStatusResponse> {
  const response = await fetch(`${API_BASE}/v1/brokers`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body?.detail ?? "Unable to load broker status");
  }
  return body as BrokerStatusResponse;
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
