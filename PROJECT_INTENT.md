# India AI Financial Analyst: Product Intent and Operating Contract

## Purpose of this document

This file is the authoritative description of what the India AI Financial Analyst is intended to become and how it must behave. It exists so that developers, coding agents, operators, and reviewers can understand the complete product goal without relying on chat history.

This document describes the target product. It does not claim that every capability below is complete in the current deployment. Runtime readiness checks, tests, and production evidence determine what is actually available.

If an implementation shortcut conflicts with this document, preserve the product intent and record the deviation rather than silently weakening the requirement.

## Product mission

Build a public, India-first equity research platform that lets anyone enter an NSE or BSE company or ticker and receive institutional-quality, source-linked research produced by a coordinated 16-role analysis system.

The platform should combine market history, company financials, exchange filings, earnings material, corporate actions, industry comparisons, Indian macro conditions, news, valuation, technical analysis, sentiment, and risk analysis. It must verify material claims against real evidence before publishing the final report.

The product is a research-intelligence system, not a broker, order-execution system, guaranteed prediction service, or substitute for regulated investment advice.

## Intended user experience

1. A visitor opens the website without being forced to sign in or sign up.
2. The visitor searches by company name or NSE/BSE ticker.
3. The interface resolves the exact listed security and shows whether it is currently supported and data-ready.
4. The visitor chooses an analysis mode and research depth.
5. The API creates a durable research job owned by that visitor's anonymous session.
6. The orchestrator selects the smallest safe agent workflow for the question.
7. Collection and specialist agents retrieve current, approved, source-linked data.
8. Deterministic calculations produce financial, technical, valuation, and risk metrics.
9. The validation agent checks material claims, calculations, freshness, contradictions, and evidence coverage.
10. The chief analyst composes a balanced final report using only claims admitted by the validation gate.
11. The interface shows progress, sources, as-of dates, confidence, warnings, bull and bear cases, risks, catalysts, and watch items.
12. The visitor can reopen their own research, export it, create watchlists, and receive monitoring updates without seeing another visitor's private activity.

If a stock or required data source is not ready, the system must explain the exact blocker immediately. It must not fabricate data, silently omit required agents, or present stale or partial results as a complete current report.

## Public access and ownership model

The product is intended to be publicly accessible without mandatory account creation. Public access does not mean shared ownership.

Every visitor must receive an isolated, signed anonymous session. Research jobs, history, watchlists, portfolios, monitoring alerts, exports, and usage limits must be associated with that session or another first-class application principal. Anonymous identities should expire under a documented retention policy and may later be attached to an optional user account if sign-in is reintroduced.

The following behaviors are prohibited:

- using one shared pseudo-user for all visitors;
- exposing one visitor's research history, watchlist, portfolio, or alert to another visitor;
- using `NULL` ownership as a substitute for public-session isolation;
- allowing public traffic to bypass quotas, rate limits, or abuse controls.

## Supported market and coverage strategy

The long-term target is broad NSE and BSE equity coverage, beginning with the genuine NSE EQ universe. Coverage should expand progressively.

Readiness must be evaluated per security and per agent. A stock may be offered for analysis only when the data required by the selected mode is complete, fresh, sourced, and internally consistent. Incomplete securities must not block otherwise complete securities, and the interface must not advertise unsupported securities as fully analyzable.

A global corpus dashboard should still report overall universe coverage, ingestion health, stale feeds, and gaps. It is an operational view, not the sole gate for every individual research request.

## Analysis modes

- **Full analysis:** complete multi-disciplinary company research.
- **Why did it move?:** explains a meaningful price move using market, event, news, peer, macro, technical, sentiment, and risk evidence.
- **What changed?:** identifies new information since the prior validated snapshot and runs the smallest safe event-specific workflow.
- **Fundamentals:** emphasizes statements, earnings quality, governance, industry position, valuation, and fundamental risks.
- **Risk:** emphasizes accounting, filings, events, macro exposure, sentiment, governance, and red flags.

## Research depths

- **Quick:** faster, narrower evidence collection and fewer optional specialist passes, while retaining the same validation and provenance standards.
- **Standard:** the default balanced institutional research workflow.
- **Deep:** broader evidence discovery, longer historical context, more comparisons, and expanded scenario work within explicit cost and time limits.

Depth changes research breadth, latency, and provider budgets. It must never lower the minimum evidence, freshness, safety, or validation standard.

## The 16-role research system

| Role | Intended responsibility |
| --- | --- |
| 1. Orchestrator | Resolves dependencies, selects the mode/depth workflow, schedules safe parallel work, applies retries/timeouts, and maintains durable job state. |
| 2. Security & Entity Intelligence | Resolves the listed company, exchange, symbol, identifiers, legal entity, security type, sector, industry, and provider mappings. |
| 3. Market & Microstructure | Analyzes source-linked price/volume history, liquidity, gaps, benchmark-relative moves, volatility, and available market context. |
| 4. Financial & Forensic Accounting | Normalizes financial statements; analyzes growth, margins, cash conversion, leverage, working capital, quality of earnings, and applicable forensic indicators. |
| 5. Filings, Governance & Corporate Actions | Reviews exchange/company filings, annual reports, governance disclosures, ownership events, related-party matters, and corporate actions. |
| 6. Earnings & Management Intelligence | Reviews results, presentations, transcripts, guidance, management commentary, estimate changes, and earnings quality. |
| 7. News & Event Intelligence | Finds fresh approved news and events, deduplicates them, connects them to the security, and separates new information from repetition. |
| 8. Web Intelligence | Performs bounded external research for missing context using approved sources and preserves retrieval metadata and citations. |
| 9. Industry & Peer Intelligence | Classifies the business, selects defensible peers, compares operating and valuation metrics, and analyzes industry structure. |
| 10. India Macro, Policy & Flows | Assesses RBI policy, rates, inflation, growth, INR, commodities, volatility, and domestic/foreign institutional flows relevant to the company. |
| 11. Valuation & Scenario Engine | Applies the appropriate valuation methods, states assumptions, provides sensitivity analysis, and constructs bull/base/bear scenarios without false precision. |
| 12. Technical & Derivatives Intelligence | Calculates reproducible indicators and trend/risk context from sufficient price history; uses derivatives data only when current approved data exists. |
| 13. Sentiment & Narrative Intelligence | Measures the direction, strength, dispersion, and change of source-backed market narratives without inventing sentiment inputs. |
| 14. Risk & Red-Flag Intelligence | Combines financial, governance, market, event, sector, and macro evidence into prioritized risks and disconfirming evidence. |
| 15. Evidence Cross-Validation | Verifies claims, calculations, sources, freshness, contradictions, and coverage; rejects or labels unsupported, stale, or contested claims. |
| 16. Chief Analyst & Research Composer | Synthesizes admitted claims into a coherent report with thesis, counter-thesis, scenarios, catalysts, risks, watch items, confidence, and limitations. |

Agents may run in parallel only where their dependencies allow it. Validation must complete before synthesis. The chief analyst must not restore a claim rejected by the validator.

## Data and evidence principles

Production research is real-data-only.

- Never insert synthetic, placeholder, generated, mock, or test data into production to make coverage look complete.
- Every material externally sourced fact must retain its provider, source URI or document identity, publication time, retrieval time, and provenance classification.
- Primary and official sources are preferred: NSE, BSE, SEBI, RBI, NSDL, company filings, annual reports, investor presentations, and approved market-data providers.
- Third-party sources may be used only when their production use and public-display rights are approved and recorded.
- Derived values are allowed only when calculated from source-linked inputs with a named/versioned formula.
- LLM output is never evidence. An LLM may explain or synthesize evidence, but it may not create missing facts.
- A missing fact remains missing. The application should lower confidence, narrow the report, or block publication as required.

`docs/DATA_PROVENANCE_POLICY.md` and `docs/AGENT_DATA_COVERAGE.md` contain the detailed enforcement contract. Where their current global-readiness wording conflicts with the progressive coverage strategy, the intended target is per-security readiness plus a separate global operational status.

## Freshness and "latest data" contract

The platform must never use the word "latest" without checking the relevant market calendar, source timestamp, and successful ingestion state.

- End-of-day prices should be current through the latest completed trading session available under the approved provider contract.
- Intraday or live prices may be labeled live only when a configured provider supplies them and their timestamps meet the live-data threshold.
- Stored or delayed prices must be labeled with the exact as-of timestamp.
- Financials must reflect the latest available reported period and identify that period.
- Filings, results, corporate actions, news, macro observations, and flow data must show publication/retrieval dates and domain-appropriate freshness.
- Each report section and material claim must carry an as-of date or traceable source timestamp.
- Feed failures or stale data must be visible to the user and operations team.

The application must use exchange-aware dates and Asia/Kolkata presentation where relevant. It must not confuse UTC storage dates with Indian trading-session dates.

## Deterministic calculations and model use

Financial ratios, forensic scores, price returns, technical indicators, peer comparisons, valuation mathematics, and confidence aggregation should be deterministic, versioned, and testable. Inputs must be traceable to evidence or other versioned derived metrics.

Language models may assist with document understanding, relevance classification, contradiction discovery, and narrative composition. Provider routing should remain configurable, with explicit budgets, timeouts, fallbacks, and kill switches. When LLM enrichment is unavailable, the system must state what did not run; it must not imply that it did.

## Final report contract

A completed report should contain, as applicable to its selected mode:

- resolved company and security identity;
- executive summary and investment thesis;
- business, industry, and peer context;
- market and technical context;
- financial performance and earnings quality;
- filings, governance, and corporate actions;
- recent events and what changed;
- Indian macro and flow exposure;
- valuation assumptions and bull/base/bear scenarios;
- catalysts, risks, red flags, and disconfirming evidence;
- watch items that could change the thesis;
- data, thesis, valuation, and catalyst confidence;
- warnings, missing information, and limitations;
- evidence catalog with working source links and timestamps;
- a clear research-only disclaimer.

Material claims should declare whether they are facts, calculations, inferences, scenarios, risks, or catalysts. Unsupported claims must not appear as established facts. Contested or stale claims must be labeled clearly.

The system must avoid a universal buy/sell score that hides assumptions. Scenario analysis, evidence, uncertainty, and user judgment are preferred over false certainty or guaranteed return language.

## Research history, monitoring, and portfolios

- Jobs must continue durably if the browser disconnects and must support safe retry without duplicate reports.
- Visitors should see only their own recent jobs and saved outputs.
- Monitoring should compare new validated snapshots with prior snapshots and alert only on meaningful changes.
- Watchlists and portfolios are research context, not brokerage accounts or trade instructions.
- Portfolio analysis should identify exposure, concentration, correlation, scenario, and thesis-change risks without placing trades.
- Exports should preserve the same claims, evidence, warnings, timestamps, and disclaimer shown in the application.

## Failure behavior

The system must fail closed for missing mandatory evidence and fail clearly for unavailable optional enrichment.

- Never convert provider failures into apparently successful empty output.
- Never publish a partial multi-agent run as a complete report.
- Distinguish invalid input, unsupported security, incomplete data, stale data, provider outage, quota exhaustion, timeout, and internal error.
- Persist durable job states and agent-level errors for debugging.
- Use bounded retries and idempotency; do not create duplicate charges or duplicate research jobs.
- Scheduled imports may finish as partial success when individual securities fail, but failures must be summarized, retried, and alerted rather than hidden or allowed to create log storms.

## Performance and operational expectations

- Normal read APIs should target a p95 response time below one second.
- Research enqueue acknowledgement should target below two seconds; long work belongs in the durable worker.
- Cached readiness checks should return in under one second.
- The interface should remain responsive and show honest agent-level progress.
- Database connections should be pooled and reused.
- Ingestion must be resumable and safe to run more than once.
- Large source documents belong in suitable object storage, with searchable metadata and evidence in the database.
- Frontend, API, and workers must expose the deployed commit SHA.

Production requires structured logs, request/job correlation IDs, error monitoring, feed-freshness monitoring, queue and worker metrics, provider health, storage monitoring, backups, tested recovery, and a documented rollback procedure.

## Security, privacy, and abuse controls

- Secrets remain server-side and must never use public frontend environment-variable prefixes.
- Public endpoints require validation, rate limits, daily quotas, and concurrency limits.
- Anonymous sessions must use secure cookies and non-guessable identifiers; stored tokens should be hashed where applicable.
- Database authorization must enforce ownership even if an API bug omits a filter.
- CORS must be limited to approved application origins.
- Source/document ingestion must treat external content as untrusted.
- Logs and monitoring must not expose credentials, private tokens, or unnecessary personal data.
- Provider keys should be scoped and rotated, and production access changes should be auditable.

## Deployment topology and release intent

The intended production topology is:

- Next.js frontend on Vercel;
- FastAPI API and durable research/ingestion workers on Railway;
- PostgreSQL, vector/evidence metadata, and application data on Supabase;
- approved external data, search, market, and LLM providers behind server-side adapters;
- Sentry or equivalent monitoring across the frontend, API, and workers;
- GitHub `main` as the source of truth for production deployments.

Every deployment should be traceable to an exact Git commit. Database migrations should be forward-safe and reviewed. Production promotion requires automated builds, tests, migration checks, smoke tests, and live health verification. A successful platform deployment does not by itself mean the research corpus is ready.

## Definition of production ready

The product may be described as working as intended only when all of the following are true for every advertised/supported security:

- the public workflow completes without mandatory sign-in;
- anonymous ownership is isolated and watchlists/history work correctly;
- all agents required by each advertised mode pass their data and execution contracts;
- every material published claim is supported by approved evidence or labeled as an inference/scenario;
- market, financial, filing, event, benchmark, and macro inputs meet documented freshness rules;
- no production readiness metric is satisfied using fake or placeholder data;
- failures, stale feeds, partial coverage, and unavailable enrichments are visible;
- representative end-to-end, browser, integration, accuracy, load, security, and recovery tests pass;
- observability and alerts cover the frontend, API, workers, queues, providers, and data freshness;
- backups and rollback procedures have been tested;
- there are no known critical or high-severity defects affecting correctness, privacy, or availability.

Broad universe coverage is a progressive goal. The product may launch with a smaller clearly identified supported universe once every stock in that subset meets the complete contract. It must not claim support for all NSE/BSE stocks until the same standard is satisfied across that advertised universe.

## Non-goals and boundaries

- No fabricated certainty, guaranteed returns, or promises that a stock will rise or fall.
- No automated trade execution unless a separately reviewed and authorized brokerage product is intentionally added.
- No claim that delayed end-of-day data is real-time.
- No use of provider data outside its applicable licensing and display rights.
- No replacement of independent investment, legal, tax, or regulated financial advice.
- No weakening of evidence or validation standards merely to produce a report faster.

## Guidance for future contributors and coding agents

Before changing the system:

1. Read this file, `docs/DATA_PROVENANCE_POLICY.md`, and `docs/AGENT_DATA_COVERAGE.md`.
2. Inspect the live implementation and readiness state; do not assume an earlier phase document proves current production readiness.
3. Preserve public access, anonymous isolation, evidence provenance, per-security readiness, and fail-closed publication behavior.
4. Add or update tests for every behavioral change.
5. Do not commit secrets, synthetic production data, or undocumented provider dependencies.
6. Record material architectural deviations and update this document when the user intentionally changes the product vision.

The central standard is simple: every report should be current, explainable, reproducible, evidence-linked, honest about uncertainty, and safe for public users to access without sharing their private activity.
