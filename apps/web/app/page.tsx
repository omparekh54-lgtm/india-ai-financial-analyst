"use client";

import { FormEvent, useState } from "react";

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

type PlanStage = {
  name: string;
  agents: string[];
  parallel: boolean;
};

type PlanResponse = {
  query: string;
  mode: string;
  stages: PlanStage[];
};

export default function HomePage() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("full_analysis");
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    setError(null);
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
      const response = await fetch(`${apiBase}/v1/research/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query.trim(), mode }),
      });
      if (!response.ok) throw new Error(`API returned ${response.status}`);
      setPlan((await response.json()) as PlanResponse);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to reach research API");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">INDIA-FIRST EQUITY INTELLIGENCE</p>
        <h1>Research a stock with a 16-agent evidence engine.</h1>
        <p className="subhead">
          Live market context, filings, fundamentals, governance, valuation, derivatives and
          source-linked AI research for NSE/BSE companies.
        </p>
        <form className="searchCard" onSubmit={submit}>
          <input
            aria-label="Company or ticker"
            placeholder="Try RELIANCE, HDFCBANK, TCS…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="submit" disabled={loading}>
            {loading ? "Planning…" : "Analyze"}
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
        {error ? <p className="errorText">{error}</p> : null}
      </section>

      {plan ? (
        <section className="panel planPanel">
          <div>
            <p className="eyebrow">LIVE EXECUTION PLAN</p>
            <h2>{plan.query}</h2>
          </div>
          <div className="planGrid">
            {plan.stages.map((stage, index) => (
              <article className="planStage" key={stage.name}>
                <span className="stageNumber">{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <strong>{stage.name}</strong>
                  <p>{stage.parallel ? "Parallel" : "Sequential"}</p>
                  <small>{stage.agents.join(" · ")}</small>
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}

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

      <section className="confidenceGrid">
        {[
          ["Data confidence", "—"],
          ["Thesis confidence", "—"],
          ["Valuation confidence", "—"],
          ["Catalyst confidence", "—"],
        ].map(([label, value]) => (
          <article key={label} className="metricCard">
            <span>{label}</span>
            <strong>{value}</strong>
          </article>
        ))}
      </section>
    </main>
  );
}
