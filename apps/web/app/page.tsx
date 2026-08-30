"use client";

import { FormEvent, useMemo, useState } from "react";

const agents = [
  "Market & Microstructure",
  "Financial & Forensics",
  "Filings & Governance",
  "Earnings Intelligence",
  "News & Events",
  "Web Intelligence",
  "Industry & Peers",
  "India Macro & Flows",
  "Valuation",
  "Technical & Derivatives",
  "Sentiment & Narrative",
  "Risk & Red Flags",
];

const modes = [
  ["Full Analysis", "full_analysis"],
  ["What Changed?", "what_changed"],
  ["Why Did It Move?", "why_did_it_move"],
] as const;

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
  freshness: string;
  excerpt?: string | null;
};

type ResearchReport = {
  query: string;
  mode: string;
  security?: Record<string, unknown> | null;
  claim_count: number;
  sections: Record<string, Claim[]>;
  special_mode?: Record<string, unknown> | null;
  evidence_catalog: Record<string, Evidence>;
  confidence: Record<string, number>;
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
  report: ResearchReport;
  agents: AgentSummary[];
};

export default function HomePage() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("full_analysis");
  const [result, setResult] = useState<ResearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
      const response = await fetch(`${apiBase}/v1/research/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query.trim(), mode }),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body?.detail ?? `API returned ${response.status}`);
      }
      setResult(body as ResearchResponse);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to run research");
    } finally {
      setLoading(false);
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
        <h1>Research a stock with a 16-agent evidence engine.</h1>
        <p className="subhead">
          NSE/BSE market context, filings, fundamentals, governance, valuation, derivatives and
          source-linked research with an independent validation gate.
        </p>
        <form className="searchCard" onSubmit={submit}>
          <input
            aria-label="Company or ticker"
            placeholder="Try RELIANCE, HDFCBANK, TCS…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="submit" disabled={loading}>
            {loading ? "Researching…" : "Analyze"}
          </button>
        </form>
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
        {loading ? (
          <p className="runStatus">Resolving security → collecting evidence → analyzing → validating → composing report…</p>
        ) : null}
        {error ? <p className="errorText">{error}</p> : null}
      </section>

      {result ? <ResearchResult result={result} /> : <PipelineOverview />}

      <section className="confidenceGrid">
        {confidenceCards.map(([label, value]) => (
          <article key={label} className="metricCard">
            <span>{label}</span>
            <strong>{typeof value === "number" && value > 0 ? `${Math.round(value * 100)}%` : "—"}</strong>
          </article>
        ))}
      </section>
    </main>
  );
}

function ResearchResult({ result }: { result: ResearchResponse }) {
  const report = result.report;
  const security = report.security ?? {};
  const name = String(security.legal_name ?? report.query);
  const symbol = security.nse_symbol ? String(security.nse_symbol) : null;
  const sections = Object.entries(report.sections ?? {}).filter(([, claims]) => claims.length > 0);

  return (
    <>
      <section className="panel resultHeader">
        <div>
          <p className="eyebrow">VALIDATED RESEARCH REPORT</p>
          <h2>{name}</h2>
          <p className="resultMeta">
            {symbol ? `NSE: ${symbol} · ` : ""}{report.claim_count} admitted claims · Job {result.job_id.slice(0, 8)}
          </p>
        </div>
        <div className="validationBadge">
          <span>Evidence gate</span>
          <strong>{String(report.validation?.evidence_coverage ?? "—")}</strong>
        </div>
      </section>

      {report.special_mode ? (
        <section className="panel specialPanel">
          <p className="eyebrow">{report.mode === "what_changed" ? "WHAT CHANGED" : "WHY DID IT MOVE"}</p>
          <SpecialMode data={report.special_mode} />
        </section>
      ) : null}

      <section className="reportGrid">
        {sections.map(([section, claims]) => (
          <article className="reportSection" key={section}>
            <p className="eyebrow">{humanize(section)}</p>
            <div className="claimList">
              {claims.map((claim) => (
                <ClaimCard key={claim.claim_id} claim={claim} evidence={report.evidence_catalog ?? {}} />
              ))}
            </div>
          </article>
        ))}
      </section>

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

      {report.research_disclaimer ? <p className="disclaimer">{report.research_disclaimer}</p> : null}
    </>
  );
}

function ClaimCard({ claim, evidence }: { claim: Claim; evidence: Record<string, Evidence> }) {
  return (
    <div className="claimCard">
      <div className="claimTopline">
        <span className={`claimStatus ${claim.status}`}>{claim.status}</span>
        <span>{Math.round(claim.confidence * 100)}% confidence</span>
      </div>
      <p>{claim.statement}</p>
      {claim.evidence_ids.length ? (
        <details>
          <summary>View evidence ({claim.evidence_ids.length})</summary>
          <div className="evidenceList">
            {claim.evidence_ids.map((id) => {
              const item = evidence[id];
              if (!item) return <small key={id}>Evidence reference {id.slice(0, 8)}</small>;
              return (
                <a key={id} href={safeExternalHref(item.source_uri)} target="_blank" rel="noreferrer">
                  <strong>{item.title ?? item.source_type}</strong>
                  <span>{item.freshness} · {item.published_at ?? "date unavailable"}</span>
                  {item.excerpt ? <small>{item.excerpt}</small> : null}
                </a>
              );
            })}
          </div>
        </details>
      ) : null}
    </div>
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
      </div>
      <div className="agentGrid">
        {agents.map((agent) => (
          <article key={agent} className="agentCard">
            <span className="statusDot" />
            <strong>{agent}</strong>
            <small>Structured output + evidence</small>
          </article>
        ))}
      </div>
    </section>
  );
}

function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function safeExternalHref(uri: string) {
  return uri.startsWith("https://") ? uri : "#";
}
