"use client";

import type { Session } from "@supabase/supabase-js";
import type { FormEvent } from "react";
import { useEffect, useMemo, useState } from "react";

import { getSupabaseBrowserClient } from "../lib/supabase";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const agents = [
  "Security & Entity Intelligence",
  "Market & Microstructure",
  "Financial & Forensics",
  "Filings & Governance",
  "Earnings Intelligence",
  "News & Events",
  "Web Intelligence",
  "Industry & Peers",
  "India Macro & Flows",
  "Valuation & Scenarios",
  "Technical & Derivatives",
  "Sentiment & Narrative",
  "Risk & Red Flags",
  "Evidence & Cross-Validation",
  "Chief Analyst",
];

const modes = [
  ["Full Analysis", "full_analysis"],
  ["What Changed?", "what_changed"],
  ["Why Did It Move?", "why_did_it_move"],
  ["Fundamentals", "fundamentals"],
  ["Risk Review", "risk"],
] as const;

const depths = [
  ["Quick", "quick", "Focused evidence and fewer optional specialist passes."],
  ["Standard", "standard", "Balanced institutional research depth."],
  ["Deep", "deep", "Expanded semantic evidence and bounded research coverage."],
] as const;

type ResearchDepth = (typeof depths)[number][1];

type Claim = {
  claim_id: string;
  agent: string;
  statement: string;
  claim_type: string;
  confidence: number;
  evidence_ids: string[];
  status: string;
  data?: Record<string, unknown>;
};

type Evidence = {
  source_type: string;
  source_uri: string;
  title?: string | null;
  published_at?: string | null;
  retrieved_at?: string | null;
  freshness: string;
  excerpt?: string | null;
  page_number?: number | null;
  section?: string | null;
  checksum?: string | null;
  content?: string | null;
};

type EvidenceExplorerClaim = {
  claim_id: string;
  agent?: string | null;
  claim_type: string;
  statement: string;
  confidence: number;
  validation_status: string;
  data?: Record<string, unknown>;
  evidence: Evidence[];
};

type EvidenceExplorerPayload = {
  job_id: string;
  claim_count: number;
  linked_evidence_count: number;
  claims: EvidenceExplorerClaim[];
};

type ResearchNarrative = {
  bull_case?: string[];
  bear_case?: string[];
  watch_items?: string[];
  confidence_note?: string;
  provider?: string;
  model?: string;
};

type ResearchReport = {
  query: string;
  mode: string;
  depth?: ResearchDepth | string;
  security?: Record<string, unknown> | null;
  claim_count: number;
  sections: Record<string, Claim[]>;
  special_mode?: Record<string, unknown> | null;
  evidence_catalog: Record<string, Evidence>;
  confidence: Record<string, number>;
  executive_summary?: string | null;
  narrative?: ResearchNarrative | null;
  validation?: Record<string, unknown>;
  warnings?: string[];
  research_disclaimer?: string;
};

type AgentSummary = {
  agent: string;
  ok: boolean;
  claim_count: number;
  evidence_count: number;
  warnings: string[];
  errors: string[];
};

type ResearchResponse = {
  job_id: string;
  security_id?: string | null;
  depth?: ResearchDepth | string;
  evidence_explorer_path?: string;
  report: ResearchReport;
  agents: AgentSummary[];
};

type ResearchJobSummary = {
  id: string;
  query: string;
  status: string;
  mode: string;
  job_metadata?: Record<string, unknown> | null;
  created_at: string;
  completed_at?: string | null;
  legal_name?: string | null;
  nse_symbol?: string | null;
  bse_code?: string | null;
  data_confidence?: number | null;
};

type StoredResearchJob = ResearchJobSummary & {
  security_id?: string | null;
  report_json?: ResearchReport | null;
};

export default function HomePage() {
  const supabase = useMemo(() => getSupabaseBrowserClient(), []);
  const [session, setSession] = useState<Session | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [authMode, setAuthMode] = useState<"sign_in" | "sign_up">("sign_in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authLoading, setAuthLoading] = useState(false);
  const [authMessage, setAuthMessage] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("full_analysis");
  const [depth, setDepth] = useState<ResearchDepth>("standard");
  const [result, setResult] = useState<ResearchResponse | null>(null);
  const [evidenceExplorer, setEvidenceExplorer] = useState<EvidenceExplorerPayload | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [history, setHistory] = useState<ResearchJobSummary[]>([]);
  const [historyLoadingId, setHistoryLoadingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

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
      setHistory([]);
      return;
    }
    void loadHistory(session.access_token).then(setHistory).catch(() => setHistory([]));
  }, [session]);

  useEffect(() => {
    if (!session || !result?.job_id) {
      setEvidenceExplorer(null);
      return;
    }
    let active = true;
    setEvidenceLoading(true);
    void loadEvidenceExplorer(session.access_token, result.job_id)
      .then((payload) => {
        if (active) setEvidenceExplorer(payload);
      })
      .catch(() => {
        if (active) setEvidenceExplorer(null);
      })
      .finally(() => {
        if (active) setEvidenceLoading(false);
      });
    return () => {
      active = false;
    };
  }, [session, result?.job_id]);

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supabase) {
      setAuthMessage("Supabase browser authentication is not configured.");
      return;
    }
    if (!email.trim() || !password) return;

    setAuthLoading(true);
    setAuthMessage(null);
    setError(null);
    try {
      if (authMode === "sign_up") {
        const { data, error: signUpError } = await supabase.auth.signUp({
          email: email.trim(),
          password,
        });
        if (signUpError) throw signUpError;
        if (data.session) {
          setAuthMessage("Account created and signed in.");
        } else {
          setAuthMessage("Account created. Check your email to confirm the account, then sign in.");
          setAuthMode("sign_in");
        }
      } else {
        const { error: signInError } = await supabase.auth.signInWithPassword({
          email: email.trim(),
          password,
        });
        if (signInError) throw signInError;
        setPassword("");
        setAuthMessage("Signed in. Your research runs are private to this account.");
      }
    } catch (authError) {
      setAuthMessage(authError instanceof Error ? authError.message : "Authentication failed");
    } finally {
      setAuthLoading(false);
    }
  }

  async function signOut() {
    if (!supabase) return;
    await supabase.auth.signOut();
    setResult(null);
    setEvidenceExplorer(null);
    setHistory([]);
    setHistoryLoadingId(null);
    setAuthMessage("Signed out.");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!query.trim()) return;
    if (!session) {
      setError("Sign in before starting a research run.");
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);
    setEvidenceExplorer(null);
    try {
      const response = await fetch(`${API_BASE}/v1/research/run`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ query: query.trim(), mode, depth }),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(apiErrorMessage(body, response.status));
      }
      setResult(body as ResearchResponse);
      setHistory(await loadHistory(session.access_token));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to run research");
    } finally {
      setLoading(false);
    }
  }

  async function openSavedResearch(jobId: string) {
    if (!session) return;
    setHistoryLoadingId(jobId);
    setError(null);
    setEvidenceExplorer(null);
    try {
      const stored = await loadResearchJob(session.access_token, jobId);
      if (!stored.report_json) {
        throw new Error("This research run does not have a saved report yet.");
      }
      const storedDepth = resolveJobDepth(stored);
      setResult({
        job_id: stored.id,
        security_id: stored.security_id ?? null,
        depth: storedDepth,
        report: stored.report_json,
        agents: [],
      });
      setQuery(stored.query);
      setMode(stored.mode);
      setDepth(storedDepth);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to open saved research");
    } finally {
      setHistoryLoadingId(null);
    }
  }

  const confidenceCards = useMemo(() => {
    const confidence = result?.report?.confidence ?? {};
    return [
      ["Data confidence", confidence.data_confidence],
      ["Thesis confidence", confidence.thesis_confidence],
      ["Valuation confidence", confidence.valuation_confidence],
      ["Catalyst confidence", confidence.catalyst_confidence],
    ] as const;
  }, [result]);

  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">INDIA-FIRST EQUITY INTELLIGENCE</p>
        <h1>Research a stock with a 16-role evidence engine.</h1>
        <p className="subhead">
          NSE/BSE market context, filings, fundamentals, governance, valuation, derivatives and
          source-linked research with an independent validation gate before publication.
        </p>

        <AuthPanel
          ready={authReady}
          configured={Boolean(supabase)}
          session={session}
          mode={authMode}
          email={email}
          password={password}
          loading={authLoading}
          message={authMessage}
          onMode={setAuthMode}
          onEmail={setEmail}
          onPassword={setPassword}
          onSubmit={submitAuth}
          onSignOut={signOut}
        />

        <form className="searchCard" onSubmit={submit}>
          <input
            aria-label="Company or ticker"
            placeholder="Try RELIANCE, HDFCBANK, TCS…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="submit" disabled={loading || !session}>
            {loading ? "Researching…" : session ? "Analyze" : "Sign in to analyze"}
          </button>
        </form>

        <div className="controlBlock">
          <div className="controlLabel">
            <strong>Analysis mode</strong>
            <span>Choose the question the agent DAG should answer.</span>
          </div>
          <div className="quickModes">
            {modes.map(([label, value]) => (
              <button
                key={value}
                type="button"
                className={mode === value ? "modeActive" : undefined}
                onClick={() => setMode(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="controlBlock depthBlock">
          <div className="controlLabel">
            <strong>Research depth</strong>
            <span>Depth changes evidence/search budgets, not validation standards.</span>
          </div>
          <div className="depthGrid">
            {depths.map(([label, value, description]) => (
              <button
                key={value}
                type="button"
                className={depth === value ? "depthCard depthActive" : "depthCard"}
                onClick={() => setDepth(value)}
              >
                <strong>{label}</strong>
                <small>{description}</small>
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <p className="runStatus">
            Resolving security → collecting source evidence → calculating → validating → composing report…
          </p>
        ) : null}
        {error ? <p className="errorText">{error}</p> : null}
      </section>

      {result ? (
        <ResearchResult
          result={result}
          evidenceExplorer={evidenceExplorer}
          evidenceLoading={evidenceLoading}
          accessToken={session?.access_token ?? null}
        />
      ) : (
        <PipelineOverview />
      )}

      {session ? (
        <RecentResearch
          jobs={history}
          loadingId={historyLoadingId}
          onOpen={(jobId) => void openSavedResearch(jobId)}
        />
      ) : null}

      <section className="confidenceGrid">
        {confidenceCards.map(([label, value]) => (
          <article key={label} className="metricCard">
            <span>{label}</span>
            <strong>
              {typeof value === "number" && value > 0 ? `${Math.round(value * 100)}%` : "—"}
            </strong>
          </article>
        ))}
      </section>
    </main>
  );
}

function AuthPanel({
  ready,
  configured,
  session,
  mode,
  email,
  password,
  loading,
  message,
  onMode,
  onEmail,
  onPassword,
  onSubmit,
  onSignOut,
}: {
  ready: boolean;
  configured: boolean;
  session: Session | null;
  mode: "sign_in" | "sign_up";
  email: string;
  password: string;
  loading: boolean;
  message: string | null;
  onMode: (mode: "sign_in" | "sign_up") => void;
  onEmail: (value: string) => void;
  onPassword: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onSignOut: () => void | Promise<void>;
}) {
  if (!ready) {
    return <div className="authCard"><span>Checking secure session…</span></div>;
  }
  if (!configured) {
    return (
      <div className="authCard authWarning">
        <strong>Authentication configuration required</strong>
        <span>Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY.</span>
      </div>
    );
  }
  if (session) {
    return (
      <div className="authCard signedInCard">
        <div>
          <span className="secureBadge">PRIVATE RESEARCH</span>
          <strong>{session.user.email ?? "Authenticated analyst"}</strong>
          <small>Runs, reports and evidence graphs are isolated to your account.</small>
        </div>
        <button type="button" className="secondaryButton" onClick={() => void onSignOut()}>
          Sign out
        </button>
      </div>
    );
  }

  return (
    <form className="authCard authForm" onSubmit={onSubmit}>
      <div className="authTabs">
        <button
          type="button"
          className={mode === "sign_in" ? "authTabActive" : undefined}
          onClick={() => onMode("sign_in")}
        >
          Sign in
        </button>
        <button
          type="button"
          className={mode === "sign_up" ? "authTabActive" : undefined}
          onClick={() => onMode("sign_up")}
        >
          Create account
        </button>
      </div>
      <div className="authFields">
        <input
          aria-label="Email"
          type="email"
          autoComplete="email"
          placeholder="analyst@example.com"
          value={email}
          onChange={(event) => onEmail(event.target.value)}
          required
        />
        <input
          aria-label="Password"
          type="password"
          autoComplete={mode === "sign_in" ? "current-password" : "new-password"}
          placeholder="Password"
          value={password}
          onChange={(event) => onPassword(event.target.value)}
          minLength={8}
          required
        />
        <button type="submit" disabled={loading}>
          {loading ? "Please wait…" : mode === "sign_in" ? "Sign in" : "Create account"}
        </button>
      </div>
      {message ? <small className="authMessage">{message}</small> : null}
    </form>
  );
}

function RecentResearch({
  jobs,
  loadingId,
  onOpen,
}: {
  jobs: ResearchJobSummary[];
  loadingId: string | null;
  onOpen: (jobId: string) => void;
}) {
  return (
    <section className="panel historyPanel">
      <div>
        <p className="eyebrow">YOUR PRIVATE RESEARCH</p>
        <h2>Recent runs</h2>
      </div>
      {jobs.length ? (
        <div className="historyList">
          {jobs.slice(0, 8).map((job) => (
            <button
              type="button"
              className="historyRow"
              key={job.id}
              disabled={job.status !== "completed" || Boolean(loadingId)}
              onClick={() => onOpen(job.id)}
            >
              <div>
                <strong>{job.legal_name ?? job.query}</strong>
                <span>
                  {job.nse_symbol ? `NSE: ${job.nse_symbol} · ` : ""}
                  {humanize(job.mode)} · {humanize(resolveJobDepth(job))}
                </span>
              </div>
              <div className="historyMeta">
                <span className={`claimStatus ${job.status === "completed" ? "verified" : "pending"}`}>
                  {loadingId === job.id ? "opening" : job.status}
                </span>
                <small>{formatDate(job.completed_at ?? job.created_at)}</small>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <p className="mutedText">No research runs yet for this account.</p>
      )}
    </section>
  );
}

function ResearchResult({
  result,
  evidenceExplorer,
  evidenceLoading,
  accessToken,
}: {
  result: ResearchResponse;
  evidenceExplorer: EvidenceExplorerPayload | null;
  evidenceLoading: boolean;
  accessToken: string | null;
}) {
  const report = result.report;
  const security = report.security ?? {};
  const name = String(security.legal_name ?? report.query);
  const symbol = security.nse_symbol ? String(security.nse_symbol) : null;
  const sections = Object.entries(report.sections ?? {}).filter(([, claims]) => claims.length > 0);
  const detailedByClaim = useMemo(() => {
    const map = new Map<string, EvidenceExplorerClaim>();
    for (const claim of evidenceExplorer?.claims ?? []) map.set(claim.claim_id, claim);
    return map;
  }, [evidenceExplorer]);
  const activeDepth = String(report.depth ?? result.depth ?? "standard");

  return (
    <>
      <section className="panel resultHeader">
        <div>
          <p className="eyebrow">VALIDATED RESEARCH REPORT</p>
          <h2>{name}</h2>
          <p className="resultMeta">
            {symbol ? `NSE: ${symbol} · ` : ""}
            {humanize(report.mode)} · {humanize(activeDepth)} depth · {report.claim_count} admitted claims · Job {result.job_id.slice(0, 8)}
          </p>
        </div>
        <div className="validationBadge">
          <span>Evidence gate</span>
          <strong>{formatCoverage(report.validation?.evidence_coverage)}</strong>
        </div>
      </section>

      <ReportActions jobId={result.job_id} accessToken={accessToken} />

      {report.executive_summary || report.narrative || report.warnings?.length ? (
        <AnalystNarrative
          summary={report.executive_summary}
          narrative={report.narrative}
          warnings={report.warnings ?? []}
        />
      ) : null}

      {report.special_mode ? (
        <section className="panel specialPanel">
          <p className="eyebrow">
            {report.mode === "what_changed" ? "WHAT CHANGED" : report.mode === "why_did_it_move" ? "WHY DID IT MOVE" : "MODE INSIGHTS"}
          </p>
          <SpecialMode data={report.special_mode} />
        </section>
      ) : null}

      <section className="reportGrid">
        {sections.map(([section, claims]) => (
          <article className="reportSection" key={section}>
            <p className="eyebrow">{humanize(section)}</p>
            <div className="claimList">
              {claims.map((claim) => (
                <ClaimCard
                  key={claim.claim_id}
                  claim={claim}
                  evidence={report.evidence_catalog ?? {}}
                  detailed={detailedByClaim.get(claim.claim_id) ?? null}
                />
              ))}
            </div>
          </article>
        ))}
      </section>

      <EvidenceExplorerPanel payload={evidenceExplorer} loading={evidenceLoading} />

      {result.agents.length ? (
        <section className="panel agentRunPanel">
          <p className="eyebrow">AGENT EXECUTION</p>
          <div className="agentGrid">
            {result.agents.map((agent) => (
              <article key={agent.agent} className="agentCard">
                <span className={agent.ok ? "statusDot" : "statusDot warningDot"} />
                <strong>{humanize(agent.agent)}</strong>
                <small>{agent.claim_count} claims · {agent.evidence_count} evidence refs</small>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {report.research_disclaimer ? <p className="disclaimer">{report.research_disclaimer}</p> : null}
    </>
  );
}

function ReportActions({ jobId, accessToken }: { jobId: string; accessToken: string | null }) {
  if (!accessToken) return null;
  return (
    <section className="reportActions" aria-label="Report exports">
      <button type="button" onClick={() => void downloadExport(accessToken, jobId, "markdown")}>
        Export Markdown
      </button>
      <button type="button" onClick={() => void downloadExport(accessToken, jobId, "json")}>
        Export JSON
      </button>
    </section>
  );
}

function AnalystNarrative({
  summary,
  narrative,
  warnings,
}: {
  summary?: string | null;
  narrative?: ResearchNarrative | null;
  warnings: string[];
}) {
  const groups = [
    ["Bull Case", narrative?.bull_case ?? []],
    ["Bear Case", narrative?.bear_case ?? []],
    ["Watch Items", narrative?.watch_items ?? []],
  ] as const;

  return (
    <section className="panel narrativePanel">
      <p className="eyebrow">CHIEF ANALYST SYNTHESIS</p>
      {summary ? <p className="narrativeSummary">{summary}</p> : null}
      {groups.some(([, items]) => items.length > 0) ? (
        <div className="narrativeGrid">
          {groups.map(([label, items]) => (
            <article className="narrativeCard" key={label}>
              <strong>{label}</strong>
              {items.length ? (
                <ul>
                  {items.map((item, index) => <li key={`${label}-${index}`}>{item}</li>)}
                </ul>
              ) : (
                <small>No validated item was admitted.</small>
              )}
            </article>
          ))}
        </div>
      ) : null}
      {narrative?.confidence_note ? (
        <p className="confidenceNote"><strong>Confidence note:</strong> {narrative.confidence_note}</p>
      ) : null}
      {warnings.length ? (
        <div className="validationWarnings">
          <strong>Validation warnings</strong>
          {warnings.map((warning, index) => <span key={index}>{warning}</span>)}
        </div>
      ) : null}
    </section>
  );
}

function ClaimCard({
  claim,
  evidence,
  detailed,
}: {
  claim: Claim;
  evidence: Record<string, Evidence>;
  detailed: EvidenceExplorerClaim | null;
}) {
  const detailedEvidence = detailed?.evidence ?? [];
  return (
    <div className="claimCard">
      <div className="claimTopline">
        <span className={`claimStatus ${claim.status}`}>{claim.status}</span>
        <span>{Math.round(claim.confidence * 100)}% confidence</span>
      </div>
      <p>{claim.statement}</p>
      {claim.evidence_ids.length ? (
        <details>
          <summary>View evidence ({Math.max(claim.evidence_ids.length, detailedEvidence.length)})</summary>
          <div className="evidenceList">
            {detailedEvidence.length
              ? detailedEvidence.map((item, index) => <EvidenceItem key={`${claim.claim_id}-${index}`} item={item} />)
              : claim.evidence_ids.map((id) => {
                  const item = evidence[id];
                  if (!item) return <small key={id}>Evidence reference {id.slice(0, 8)}</small>;
                  return <EvidenceItem key={id} item={item} />;
                })}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function EvidenceItem({ item }: { item: Evidence }) {
  const href = safeExternalHref(item.source_uri);
  const locator = [
    item.page_number ? `Page ${item.page_number}` : null,
    item.section ? humanize(item.section) : null,
  ].filter(Boolean).join(" · ");
  const body = item.content ?? item.excerpt;

  return (
    <a href={href} target={href === "#" ? undefined : "_blank"} rel={href === "#" ? undefined : "noreferrer"}>
      <strong>{item.title ?? item.source_type}</strong>
      <span>
        {humanize(item.freshness || "unknown")}
        {item.published_at ? ` · ${formatDate(item.published_at)}` : ""}
        {locator ? ` · ${locator}` : ""}
      </span>
      {body ? <small>{body}</small> : null}
      {item.checksum ? <code>checksum {item.checksum.slice(0, 12)}…</code> : null}
    </a>
  );
}

function EvidenceExplorerPanel({
  payload,
  loading,
}: {
  payload: EvidenceExplorerPayload | null;
  loading: boolean;
}) {
  return (
    <section className="panel evidenceExplorerPanel">
      <div className="evidenceExplorerHeader">
        <div>
          <p className="eyebrow">CLAIM-LEVEL EVIDENCE EXPLORER</p>
          <h2>Trace conclusions back to source evidence.</h2>
        </div>
        <div className="evidenceCount">
          <strong>{payload?.linked_evidence_count ?? "—"}</strong>
          <span>linked chunks</span>
        </div>
      </div>
      {loading ? <p className="mutedText">Loading persisted evidence graph…</p> : null}
      {!loading && payload ? (
        <div className="explorerList">
          {payload.claims.map((claim) => (
            <details className="explorerClaim" key={claim.claim_id}>
              <summary>
                <span className={`claimStatus ${claim.validation_status}`}>{claim.validation_status}</span>
                <strong>{claim.statement}</strong>
                <small>{claim.evidence.length} linked sources</small>
              </summary>
              <div className="evidenceList explorerEvidenceList">
                {claim.evidence.length
                  ? claim.evidence.map((item, index) => <EvidenceItem key={`${claim.claim_id}-e-${index}`} item={item} />)
                  : <small>No persisted evidence chunk is linked to this claim.</small>}
              </div>
            </details>
          ))}
        </div>
      ) : null}
      {!loading && !payload ? (
        <p className="mutedText">No persisted claim-evidence graph is available for this report yet.</p>
      ) : null}
    </section>
  );
}

function SpecialMode({ data }: { data: Record<string, unknown> }) {
  const drivers = Array.isArray(data.candidate_drivers) ? data.candidate_drivers : null;
  if (drivers) {
    return (
      <div className="driverList">
        {drivers.length ? drivers.map((driver, index) => (
          <article key={index} className="driverCard">
            <strong>#{index + 1} {String((driver as Record<string, unknown>).type ?? "candidate driver")}</strong>
            <p>{String((driver as Record<string, unknown>).detail ?? "")}</p>
          </article>
        )) : <p>No sufficiently strong candidate driver was identified.</p>}
        <small>{String(data.note ?? "")}</small>
      </div>
    );
  }

  const groups = ["new_risks", "resolved_risks", "new_catalysts", "resolved_catalysts"];
  return (
    <div className="changeGrid">
      {groups.map((key) => {
        const values = Array.isArray(data[key]) ? data[key] as Array<Record<string, unknown>> : [];
        return (
          <article key={key} className="changeCard">
            <strong>{humanize(key)}</strong>
            {values.length ? values.map((item, index) => (
              <p key={index}>{String(item.statement ?? item.title ?? "Material change")}</p>
            )) : <small>None detected</small>}
          </article>
        );
      })}
    </div>
  );
}

function PipelineOverview() {
  return (
    <section className="panel">
      <div>
        <p className="eyebrow">RESEARCH PIPELINE</p>
        <h2>Deterministic numbers. AI reasoning. Independent validation.</h2>
        <p className="mutedText">
          The 16 logical roles communicate through typed state and evidence. Quick may skip optional
          specialists, but every published report still passes the evidence validator before Chief Analyst synthesis.
        </p>
      </div>
      <div className="agentGrid">
        {agents.map((agent) => (
          <article key={agent} className="agentCard">
            <span className="statusDot" />
            <strong>{agent}</strong>
            <small>Structured output + evidence contract</small>
          </article>
        ))}
      </div>
    </section>
  );
}

async function loadHistory(accessToken: string): Promise<ResearchJobSummary[]> {
  const response = await fetch(`${API_BASE}/v1/research/jobs?limit=20`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) return [];
  const body = await response.json();
  return Array.isArray(body?.jobs) ? body.jobs as ResearchJobSummary[] : [];
}

async function loadResearchJob(accessToken: string, jobId: string): Promise<StoredResearchJob> {
  const response = await fetch(`${API_BASE}/v1/research/jobs/${encodeURIComponent(jobId)}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(apiErrorMessage(body, response.status));
  }
  return body as StoredResearchJob;
}

async function loadEvidenceExplorer(
  accessToken: string,
  jobId: string,
): Promise<EvidenceExplorerPayload> {
  const response = await fetch(`${API_BASE}/v1/research/jobs/${encodeURIComponent(jobId)}/evidence`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(apiErrorMessage(body, response.status));
  }
  return body as EvidenceExplorerPayload;
}

async function downloadExport(
  accessToken: string,
  jobId: string,
  format: "markdown" | "json",
) {
  const response = await fetch(
    `${API_BASE}/v1/research/jobs/${encodeURIComponent(jobId)}/export?format=${format}`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(apiErrorMessage(body, response.status));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `india-equity-research-${jobId}.${format === "json" ? "json" : "md"}`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function resolveJobDepth(job: ResearchJobSummary | StoredResearchJob): ResearchDepth {
  const reportDepth = "report_json" in job ? job.report_json?.depth : undefined;
  const metadataDepth = job.job_metadata?.analysis_depth;
  const candidate = String(reportDepth ?? metadataDepth ?? "standard");
  return candidate === "quick" || candidate === "deep" ? candidate : "standard";
}

function apiErrorMessage(body: unknown, status: number) {
  if (body && typeof body === "object") {
    const detail = (body as Record<string, unknown>).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const message = (detail as Record<string, unknown>).message;
      if (typeof message === "string") return message;
    }
  }
  return `API returned ${status}`;
}

function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function safeExternalHref(uri: string) {
  return uri.startsWith("https://") ? uri : "#";
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatCoverage(value: unknown) {
  if (typeof value !== "number") return "—";
  return `${Math.round(value * 100)}%`;
}
