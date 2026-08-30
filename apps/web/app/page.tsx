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

export default function HomePage() {
  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">INDIA-FIRST EQUITY INTELLIGENCE</p>
        <h1>Research a stock with a 16-agent evidence engine.</h1>
        <p className="subhead">
          Live market context, filings, fundamentals, governance, valuation, derivatives and
          source-linked AI research for NSE/BSE companies.
        </p>
        <form className="searchCard">
          <input aria-label="Company or ticker" placeholder="Try RELIANCE, HDFCBANK, TCS…" />
          <button type="button">Analyze</button>
        </form>
        <div className="quickModes">
          <button type="button">Full Analysis</button>
          <button type="button">What Changed?</button>
          <button type="button">Why Did It Move?</button>
        </div>
      </section>

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
