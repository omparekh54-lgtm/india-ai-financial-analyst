# Phases 13–18 completion record

This document separates **implementation completion** from **live production activation**. A green CI run proves the software contracts below; it does not fabricate a green real-data corpus or a deployment that has not actually been provisioned.

## Phase 13 — Live database and security boundary

**Implementation: complete. Live schema/security: complete.**

- Event-triggered research idempotency is enforced at the database layer.
- Private `watchlists` and `watchlist_items` are live with owner-only RLS.
- Watchlist RLS uses scalar `select auth.uid()` evaluation to avoid row-by-row auth initialization overhead.
- Database preflight requires both watchlist tables and their owner policies.
- Backend-owned corpus/broker tables intentionally remain inaccessible to `anon` and `authenticated`; the application backend is the data boundary.
- No production readiness rule grants direct client access to evidence, financial, market, broker-secret or ingestion tables.

## Phase 14 — Real production corpus pipeline

**Implementation: complete. Live corpus population: pending production execution.**

The fail-closed production corpus sequence is:

1. genuine NSE EQ universe and official NSE classification provenance,
2. exact provider-instrument mapping,
3. source-linked India market/macro context,
4. listing-age-aware sourced market history,
5. official NSE financial-result XBRL ingestion,
6. deterministic financial facts plus filing/earnings evidence,
7. deterministic source-linked peer/security metrics,
8. authoritative readiness postflight.

The market/macro layer bootstraps:

- USD/INR and Brent through approved FRED series,
- explicit official RBI source inputs for repo rate, CPI YoY and IIP YoY,
- NIFTY 50 and India VIX benchmark history,
- India VIX normalized macro observation,
- FII/FPI and DII cash flows,
- RBI India 10Y yield.

Official RBI URL ingestion is HTTPS/domain allowlisted and SSRF guarded. There is no synthetic, estimated, placeholder or silent paid-provider fallback.

`bootstrap_production_research_corpus.py` is resumable at `market`, `financials`, `peer_metrics` or `readiness`. Market-stage macro inputs are only required when the market stage will actually execute.

## Phase 15 — Authoritative 16-agent data readiness

**Implementation: complete. Live readiness: red until Phase 14 is executed with real data.**

`run_agent_readiness_gate.py` is the authoritative read-only production gate. It requires both the corpus-level and all 16 agent-level data contracts to pass.

Important policies:

- minimum 1,000 supported NSE EQ securities,
- 100% provider mapping and provenance-linked official classification,
- listing-age-aware financial history with at least six canonical fact types and per-security freshness,
- parsed recent primary filing and earnings evidence,
- listing-age-aware daily market history and fresh benchmark history,
- at least three recent auditable peer/security metrics per supported security,
- NIFTY 50 and India VIX sourced benchmark histories,
- all nine required macro/flow series,
- no synthetic/mock/sample provenance,
- all material facts/events/bars/macros/metrics source-linked,
- no enabled unapproved official feed.

`run_agent_coverage_gap_report.py` uses the same authoritative policies; legacy universal 8-period/200-bar assumptions no longer define readiness for recent listings.

## Phase 16 — End-to-end 16-agent research runtime

**Implementation: complete. Full-universe live validation: pending live corpus/deployment.**

The runtime enforces:

- specialist agents publish typed state and evidence rather than a free-form shared history,
- Agent 15 validates/recomputes/reconciles claims,
- one bounded repair pass may run before final synthesis,
- Agent 16 cannot execute before Agent 15,
- synthesis receives only `verified`, `supported` or `inferred` claims,
- `contested`, `unsupported` and `stale` claims are excluded from final synthesis,
- deterministic calculations remain code-owned rather than delegated to an LLM.

A complete live end-to-end claim cannot be made until the real corpus gate is green.

## Phase 17 — Research terminal, evidence UX and watchlists

**Implementation: complete. Production browser QA: pending deployment.**

The web application includes:

- research terminal and special analysis modes,
- source/freshness-aware report sections,
- claim/evidence explorer,
- four confidence dimensions,
- saved research workflow,
- private `/watchlists` workspace,
- create/delete watchlists,
- add a security by NSE symbol, company name, BSE code or ISIN through canonical resolver logic,
- remove securities,
- opt in/out of event-triggered research per security.

Watchlist ownership remains private at both API and database boundaries.

## Phase 18 — Production hardening, release QA and operator controls

**Implementation/tooling: complete. Deployment activation: pending dedicated hosting target and secrets.**

Production QA includes:

- structural configuration/database preflight,
- authoritative corpus + 16-agent readiness,
- authenticated GET-only deployment smoke,
- two-user ownership-isolation verification,
- bounded read-only load probe,
- fail-closed aggregate release gate.

Two manual GitHub Actions workflows are included:

- `.github/workflows/production-corpus.yml`
- `.github/workflows/production-release-gate.yml`

Both are `workflow_dispatch` only, use `contents: read`, use a `production` environment, require explicit typed confirmation, keep credentials in environment secrets, use non-cancelling production concurrency locks, and never run automatically on a normal push or pull request.

The corpus workflow keeps `FREE_ONLY=true`, external LLM calls off and event research off during corpus construction. The release workflow executes the existing fail-closed release gate and never accepts access tokens as CLI arguments.

## Current live activation blockers

The software must not label the live environment production-ready until these external activation requirements are satisfied:

- the live corpus is expanded from the small development seed to at least 1,000 genuine NSE EQ securities,
- official classification and exact market-data mappings reach 100%,
- market and benchmark histories are populated,
- financial/filing/earnings and peer-metric coverage reach the production contract,
- all nine source-linked macro/flow series are present,
- production provider/environment secrets are configured in the protected execution environment,
- a dedicated API/web deployment for this repository exists,
- short-lived smoke/auth-isolation test-user tokens and an existing owner research job are available,
- the production release workflow completes with `release_ready=true`.

These are **activation/data requirements**, not missing fallback code. The project intentionally fails closed rather than inserting fake data or silently downgrading the contract.

## Completion definition

Phases 13–18 are considered **software implementation complete** when CI is green for API lint/typecheck/tests, web production build and production container configuration, and the manual production workflows retain their safety contracts.

They are considered **live production activated** only after the real corpus and deployed release gates both pass. These two states must remain distinct in operator communication and release decisions.
