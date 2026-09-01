"use client";

import type { Session } from "@supabase/supabase-js";
import type { FormEvent } from "react";
import { useEffect, useMemo, useState } from "react";

import { getSupabaseBrowserClient } from "../../lib/supabase";
import styles from "./watchlists.module.css";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type WatchlistItem = {
  security_id: string;
  legal_name?: string | null;
  nse_symbol?: string | null;
  bse_code?: string | null;
  isin?: string | null;
  sector?: string | null;
  industry?: string | null;
  notes?: string | null;
  event_research_enabled: boolean;
  added_at?: string | null;
};

type Watchlist = {
  id: string;
  name: string;
  created_at?: string | null;
  updated_at?: string | null;
  items: WatchlistItem[];
};

type WatchlistPayload = {
  count: number;
  watchlists: Watchlist[];
};

export default function WatchlistsPage() {
  const supabase = useMemo(() => getSupabaseBrowserClient(), []);
  const [session, setSession] = useState<Session | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [watchlists, setWatchlists] = useState<Watchlist[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [securityQuery, setSecurityQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!supabase) {
      setAuthReady(true);
      return;
    }
    let active = true;
    void supabase.auth.getSession().then(({ data }) => {
      if (!active) return;
      setSession(data.session);
      setAuthReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setAuthReady(true);
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
    };
  }, [supabase]);

  useEffect(() => {
    if (!session) {
      setWatchlists([]);
      setSelectedId(null);
      return;
    }
    void refresh(session.access_token);
  }, [session]);

  const selected = watchlists.find((watchlist) => watchlist.id === selectedId) ?? watchlists[0] ?? null;

  async function refresh(token: string) {
    setError(null);
    const response = await fetch(`${API_BASE}/v1/watchlists`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    const body = await response.json();
    if (!response.ok) throw new Error(apiErrorMessage(body, response.status));
    const payload = body as WatchlistPayload;
    setWatchlists(payload.watchlists);
    setSelectedId((current) => {
      if (current && payload.watchlists.some((watchlist) => watchlist.id === current)) return current;
      return payload.watchlists[0]?.id ?? null;
    });
  }

  async function createWatchlist(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !newName.trim()) return;
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/v1/watchlists`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ name: newName.trim() }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(body, response.status));
      setNewName("");
      setSelectedId(String(body.id));
      await refresh(session.access_token);
      setMessage("Watchlist created.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to create watchlist");
    } finally {
      setLoading(false);
    }
  }

  async function deleteWatchlist(watchlistId: string) {
    if (!session) return;
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/v1/watchlists/${watchlistId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      const body = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(body, response.status));
      await refresh(session.access_token);
      setMessage("Watchlist deleted.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to delete watchlist");
    } finally {
      setLoading(false);
    }
  }

  async function addSecurity(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !selected || !securityQuery.trim()) return;
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE}/v1/watchlists/${selected.id}/items/resolve`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          query: securityQuery.trim(),
          event_research_enabled: true,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(body, response.status));
      setSecurityQuery("");
      await refresh(session.access_token);
      setMessage("Security added with event research enabled.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to add security");
    } finally {
      setLoading(false);
    }
  }

  async function updateItem(item: WatchlistItem, eventResearchEnabled: boolean) {
    if (!session || !selected) return;
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/v1/watchlists/${selected.id}/items`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          security_id: item.security_id,
          notes: item.notes ?? null,
          event_research_enabled: eventResearchEnabled,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(body, response.status));
      await refresh(session.access_token);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to update watchlist item");
    } finally {
      setLoading(false);
    }
  }

  async function removeItem(securityId: string) {
    if (!session || !selected) return;
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/v1/watchlists/${selected.id}/items/${securityId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      const body = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(body, response.status));
      await refresh(session.access_token);
      setMessage("Security removed.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to remove security");
    } finally {
      setLoading(false);
    }
  }

  if (!authReady) {
    return <main className={styles.shell}>Checking session…</main>;
  }

  if (!session) {
    return (
      <main className={styles.shell}>
        <section className={styles.hero}>
          <div>
            <p className={styles.eyebrow}>PRIVATE EVENT RESEARCH</p>
            <h1>Watchlists</h1>
            <p>Sign in on the research terminal first. Watchlists are private to your account.</p>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className={styles.shell}>
      <section className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>PRIVATE EVENT RESEARCH</p>
          <h1>Watchlists</h1>
          <p>
            Track Indian listed companies and opt into event-triggered What Changed? research for
            material exchange disclosures. No research job is created when event research is off.
          </p>
        </div>
      </section>

      {message ? <p className={styles.message}>{message}</p> : null}
      {error ? <p className={styles.error}>{error}</p> : null}

      <div className={styles.grid}>
        <aside className={styles.card}>
          <form className={styles.form} onSubmit={createWatchlist}>
            <input
              aria-label="New watchlist name"
              placeholder="e.g. Core Holdings"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              maxLength={80}
            />
            <button className={styles.button} type="submit" disabled={loading || !newName.trim()}>
              Create
            </button>
          </form>

          <div className={styles.watchlistList}>
            {watchlists.map((watchlist) => (
              <button
                key={watchlist.id}
                type="button"
                className={`${styles.listButton} ${selected?.id === watchlist.id ? styles.active : ""}`}
                onClick={() => setSelectedId(watchlist.id)}
              >
                <span>{watchlist.name}</span>
                <span>{watchlist.items.length}</span>
              </button>
            ))}
          </div>
          {watchlists.length === 0 ? <p className={styles.empty}>Create your first watchlist.</p> : null}
        </aside>

        <section className={styles.card}>
          {selected ? (
            <>
              <div className={styles.headerRow}>
                <div>
                  <h2>{selected.name}</h2>
                  <p className={styles.meta}>{selected.items.length} tracked securities</p>
                </div>
                <button
                  type="button"
                  className={styles.danger}
                  onClick={() => void deleteWatchlist(selected.id)}
                  disabled={loading}
                >
                  Delete watchlist
                </button>
              </div>

              <form className={styles.form} onSubmit={addSecurity}>
                <input
                  aria-label="Company, NSE symbol, BSE code or ISIN"
                  placeholder="RELIANCE, HDFCBANK, TCS…"
                  value={securityQuery}
                  onChange={(event) => setSecurityQuery(event.target.value)}
                  maxLength={160}
                />
                <button className={styles.button} type="submit" disabled={loading || !securityQuery.trim()}>
                  Add security
                </button>
              </form>

              <div className={styles.itemList}>
                {selected.items.map((item) => (
                  <article className={styles.itemRow} key={item.security_id}>
                    <div>
                      <div className={styles.itemTitle}>
                        {item.nse_symbol || item.bse_code || item.legal_name || item.security_id}
                      </div>
                      <div className={styles.meta}>
                        {[item.legal_name, item.sector, item.industry].filter(Boolean).join(" · ")}
                      </div>
                    </div>
                    <div className={styles.actions}>
                      <label className={styles.toggle}>
                        <input
                          type="checkbox"
                          checked={item.event_research_enabled}
                          disabled={loading}
                          onChange={(event) => void updateItem(item, event.target.checked)}
                        />
                        Event research
                      </label>
                      <button
                        type="button"
                        className={styles.danger}
                        onClick={() => void removeItem(item.security_id)}
                        disabled={loading}
                      >
                        Remove
                      </button>
                    </div>
                  </article>
                ))}
                {selected.items.length === 0 ? (
                  <p className={styles.empty}>Add an NSE symbol or company name to start tracking it.</p>
                ) : null}
              </div>
            </>
          ) : (
            <p className={styles.empty}>Create a watchlist to start tracking securities.</p>
          )}
        </section>
      </div>
    </main>
  );
}

function apiErrorMessage(body: unknown, status: number): string {
  if (typeof body !== "object" || body === null) return `Request failed (${status})`;
  const record = body as Record<string, unknown>;
  const detail = record.detail;
  if (typeof detail === "string") return detail;
  if (typeof detail === "object" && detail !== null) {
    const message = (detail as Record<string, unknown>).message;
    if (typeof message === "string") return message;
  }
  return `Request failed (${status})`;
}
