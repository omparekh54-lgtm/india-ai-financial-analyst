# Production agent data coverage contract

This document defines the data conditions required before the India AI Financial Analyst may describe its production research corpus as ready.

## Non-negotiable provenance rule

Production readiness must never be achieved with synthetic, mock, fake, dummy, fixture, sample, generated or placeholder research data.

Every material financial fact, corporate event, security price bar, benchmark bar, macro observation and security/comparable metric must be linked to a source row. Official sources are preferred. Non-official reference imports require an explicit licensing or source-governance approval reference. Enabled exchange feeds that still require licensing approval block production readiness.

Development unit-test fixtures are permitted only inside tests; they are not corpus data and must never be inserted to satisfy a production readiness gate.

## Supported-universe standard

The production corpus must contain at least 1,000 genuine NSE EQ securities before the universe gate passes. For the supported universe:

- provider-instrument mapping coverage: 100%
- sector and industry classification coverage: 100%
- sourced financial-history coverage: 100%
- recent parsed primary-filing coverage: 100%
- recent earnings-evidence coverage: 100%
- sourced technical-history coverage: 100%
- recent sourced comparable/security-metric coverage: 100%

The application reports the numerator, denominator and percentage rather than rounding partial coverage to ready.

## Per-agent requirements

| Agent | Production data contract |
| --- | --- |
| 1. Orchestrator | Every required specialist below must be data-ready. |
| 2. Entity Intelligence | >=1,000 NSE EQ securities and 100% provider-instrument mapping. |
| 3. Market & Microstructure | 100% mappings; >=200 sourced daily bars per supported security within the historical window; sourced NIFTY 50 and India VIX history; latest security and benchmark history no more than 7 days old. |
| 4. Financial & Forensics | Every supported security has >=8 sourced financial periods and >=6 canonical fact types; latest financial period no more than 200 days old. |
| 5. Filings & Governance | Every supported security has non-empty parsed primary filing evidence published/retrieved within 400 days. |
| 6. Earnings Intelligence | Financial-history contract passes and every supported security has parsed financial-results, call, transcript, presentation or XBRL evidence within 220 days. |
| 7. News & Events | Approved fresh external-data acquisition is enabled and Tavily credentials are configured. News is acquired on demand; fabricated or pre-generated news is prohibited. |
| 8. Web Intelligence | Same approved fresh-acquisition requirement as News. |
| 9. Industry & Peers | 100% sector/industry classification and at least 3 recent sourced comparable/security metrics for every supported security. |
| 10. India Macro & Flows | Source-linked coverage for repo rate, India 10Y yield, USD/INR, Brent, India VIX, CPI YoY, IIP YoY, FII cash net flow and DII cash net flow; macro data <=45 days stale; sourced NIFTY 50 and India VIX benchmark history. |
| 11. Valuation | Complete financial history, recent comparable metrics and security market history for every supported security. |
| 12. Technical & Derivatives | >=200 sourced daily bars for every supported security, sourced NIFTY 50/India VIX history and market data <=7 days stale. |
| 13. Sentiment & Narrative | Approved fresh News/Web acquisition is configured; synthetic narrative/sentiment inputs are prohibited. |
| 14. Risk & Red Flags | Complete financial and recent filing coverage plus approved fresh event/news/web acquisition. |
| 15. Evidence Cross-Validation | Real source rows and parsed evidence chunks exist; global provenance checks pass. |
| 16. Chief Analyst | All required upstream specialists and Agent 15 are data-ready. |

Optional LLM enrichment is not itself a hard data requirement because specialist agents retain deterministic logic. When disabled, readiness reports a warning rather than pretending the LLM ran. Live Upstox overlay is also optional when complete source-linked stored history is available; delayed/stored data must remain labeled accordingly.

## Current connected-database snapshot

The connected Supabase instance must be assessed from the live readiness query; this document must not be edited to fake a green state. At the time this contract was introduced, the live database contained only a small real integration subset and therefore correctly failed production readiness.

The initial observed gaps were:

- 5 NSE EQ securities / 5 provider-mapped / 5 classified
- 1 security meeting the strict financial-history contract
- 0 securities meeting recent parsed filing coverage
- 0 securities meeting recent earnings-evidence coverage
- 0 securities meeting the >=200 sourced daily-bar contract
- 1 security meeting recent peer-metric coverage
- no sourced NIFTY 50 or India VIX benchmark histories
- macro series present: Brent, CPI YoY, IIP YoY, repo rate and USD/INR
- macro series still required: India 10Y yield, India VIX, FII cash net flow and DII cash net flow

These numbers are a snapshot, not seed targets. The production gate remains red until genuine source-backed data satisfies the full contract.

## Enforcement surfaces

The same contract is enforced in three places:

1. `python scripts/run_data_coverage_audit.py` for operator/CI-style auditing.
2. Authenticated `GET /v1/system/data-readiness` for the dashboard and operational inspection.
3. `POST /v1/research/run` in production, which returns HTTP 503 and the blocking-agent list until the global corpus and required agent contracts pass.

The dashboard may show `16/16 AGENTS READY` only when these server-side checks are actually green.
