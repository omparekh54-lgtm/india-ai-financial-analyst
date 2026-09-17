# AI Agent Instructions: Production Upgrade Program

## Purpose

This file gives IDE coding agents the execution context for upgrading the India AI Financial Analyst from a deployed prototype into a genuinely usable production product.

Read this file before changing code. Also read the following sources before implementation:

1. `PROJECT_INTENT.md` — authoritative product vision and operating contract.
2. `docs/DATA_PROVENANCE_POLICY.md` — evidence and source requirements.
3. `docs/AGENT_DATA_COVERAGE.md` — agent-level data requirements.
4. Relevant existing runbooks and phase documents for the area being changed.

This file explains the upgrade process and engineering priorities. It does not override `PROJECT_INTENT.md`. If these instructions conflict with the product intent, preserve the product intent and report the conflict.

## Collaboration model

The repository is being upgraded through two coordinated work surfaces.

### IDE work

Antigravity or another IDE coding agent performs repository work:

- Inspect existing implementation before editing.
- Create a dedicated branch for each bounded phase.
- Implement code, migrations, tests, workflows and documentation.
- Run relevant local verification.
- Commit changes and provide an exact completion report.
- Stop before merging, applying production migrations or changing production services.

### External platform work

The ChatGPT operator reviews the GitHub change and then handles production operations using connected tools:

- Review commits, diffs, tests and CI.
- Merge approved pull requests.
- Apply migrations to Supabase.
- Configure and verify GitHub Actions.
- Deploy and inspect Railway services.
- Deploy and inspect Vercel.
- Verify Sentry and production logs.
- Run live acceptance checks.
- Return production failures to the IDE as the next bounded repair prompt.

The normal loop is:

1. IDE implementation.
2. Local tests.
3. Commit and handoff report.
4. GitHub review.
5. External deployment/configuration.
6. Production verification.
7. IDE repair if a gate fails.
8. Proceed only after the phase gate passes.

Do not bypass this loop by applying undocumented manual production fixes.

## Fixed product decisions

Preserve all of these decisions unless the user explicitly changes them:

- The application is publicly accessible without mandatory sign-in.
- Each visitor receives an isolated anonymous principal/session.
- The production data profile is end-of-day or previous-session data, not guaranteed real-time or intraday.
- Market data must show an exact as-of timestamp and must never be mislabeled as live.
- The project should remain within no-paid-plan constraints wherever technically possible.
- Production research must use real data only.
- Missing data must remain missing; never manufacture a successful readiness state.
- A stock is analyzable only when that stock passes the requirements for the selected mode.
- One incomplete stock must not block an otherwise complete stock.
- Global readiness remains an operational dashboard, not the only research gate.
- Evidence validation must finish before Chief Analyst synthesis.
- The Chief Analyst must not restore claims rejected by validation.
- The initial fully supported launch universe is the NIFTY 50.
- Coverage expands progressively only after each additional security passes readiness.
- The long-term target remains the complete genuine NSE EQ universe and later eligible BSE coverage.
- Upstox/intraday functionality is not required for the EOD launch.
- Do not introduce a paid dependency without explicit user approval.

## Current verified production baseline

This snapshot was verified on 17 September 2026 and must be rechecked rather than assumed current in later work.

### Infrastructure currently online

- GitHub source of truth: `omparekh54-lgtm/india-ai-financial-analyst`.
- Vercel public production URL: `https://india-ai-financial-analyst.vercel.app`.
- FastAPI is deployed on Railway.
- Supabase live project is active and healthy.
- Anonymous-principal migration 27 is applied.
- Current main deployment commit at the time of this snapshot: `533e12b`.
- Current CI passed with 515 API tests.
- Five Railway services reported successful deployments.
- The Railway Yahoo Finance EOD job was refreshing supported history.

### Corpus and execution gaps at that snapshot

- 2,302 genuine NSE EQ securities existed.
- Market bars covered 2,060 securities.
- Approximately 708,258 market-bar rows existed.
- Market data had current daily refresh activity.
- Classification had been attempted for 746 securities.
- Only five securities had both sector and industry populated.
- Zero securities satisfied the required complete official four-tier classification contract.
- Financial facts covered only one security.
- Security metrics covered only five securities.
- Evidence chunks: zero.
- Research jobs: zero.
- Agent runs: zero.
- Claims: zero.
- Research reports: zero.
- Only one corporate event existed.
- NIFTY 50 and India VIX benchmark history was stale.
- Scheduled free-tier GitHub data jobs were failing because the production `DATABASE_URL` secret was unavailable.
- The readiness endpoint had taken about 7.7 seconds and a client closed the request.
- Yahoo-derived sources were marked for internal research use and did not have commercial display approval.

These numbers are diagnostic context, not fixtures or acceptance targets. Query production again before relying on them.

## Central production problem

The website, API and database are deployed, but a successful deployment is not equivalent to a usable research product.

The API correctly fails closed before enqueueing research when the requested security lacks required classification, financial history, filings, earnings evidence, peer metrics, fresh benchmarks, macro context or approved provenance. With the baseline above, no stock could pass the full production contract, so no genuine multi-agent report could complete.

Do not “fix” this by weakening the gate. Fix the data pipelines, performance and durable research execution.

## Engineering principles

### Preserve safety and provenance

- Never add synthetic, mock, placeholder, generated or fixture data to production.
- Every externally sourced material fact must retain source identity, URI, publication time, retrieval time and provenance class.
- Every derived value must identify its formula and versioned inputs.
- Treat downloaded documents and external text as untrusted content.
- LLM output is never evidence.
- Do not expose restricted data merely because it is technically available.
- Do not mark a source commercially approved without an approval reference.

### Preserve user isolation

- Never use one shared public user.
- Never use NULL ownership as public ownership.
- Scope research jobs, reports, watchlists, portfolios and alerts to the current principal.
- Add tests with two anonymous users for every ownership-sensitive feature.
- Do not log cookies, bearer tokens, database URLs or provider keys.

### Preserve fail-closed behavior

- Unsupported security: explicit 404-style product response.
- Supported but incomplete security: explicit readiness response with blockers.
- Provider failure: explicit provider failure, not successful empty output.
- Partial agent run: internal partial state only, never publish as a completed report.
- Stale required input: block or clearly narrow the product output according to the relevant contract.

### Keep changes bounded

- Use small branches and pull requests.
- Do not combine unrelated refactors with a production repair.
- Preserve existing working behavior unless the phase requires a deliberate change.
- Add tests before or with behavior changes.
- Do not merge or deploy from the IDE.
- Do not commit secrets, local databases, generated archives or production data exports.

## Upgrade phases

Proceed in this order. A later phase may be prepared, but it must not be declared complete until earlier gates are satisfied.

## Phase 1 — Readiness and API performance foundation

### Objective

Replace expensive request-time corpus scans with fast, stored per-security readiness while preserving all evidence and freshness rules.

### IDE work

- Inspect the existing readiness, security-readiness, database and API modules.
- Introduce a forward-safe migration for a `security_readiness_status` table or an equivalently justified design.
- Store security ID, ready state, supported modes, blocking agents, blocker details, latest market session, latest financial period, latest filing/earnings evidence, last evaluation time and rule version.
- Build an incremental readiness evaluator for one security.
- Recalculate only securities affected by an ingestion change.
- Keep a separate global corpus summary.
- Change normal public readiness endpoints to use prepared status rather than scanning the complete market history.
- Reuse a process-level async database engine/pool instead of creating a new engine for each request.
- Add timeouts, structured errors and request correlation IDs.
- Add indexes driven by actual query paths.
- Add tests proving that an incomplete security does not block a complete one.
- Add tests proving that stored readiness cannot bypass current provenance and freshness rules.

### Gate

- Readiness p95 below one second under a representative dataset.
- Security search below 500 ms.
- Research enqueue acknowledgement below two seconds.
- No normal public request scans every market-bar row.
- Existing correctness tests remain green.

## Phase 2 — Scheduled data workflow repair

### Objective

Make all bounded maintenance jobs resumable, observable and safe on free infrastructure.

### IDE work

Create or refine independent workflows for:

- EOD market refresh.
- Benchmark and macro refresh.
- Classification backfill.
- Financial-results backfill.
- Filings and evidence backfill.
- Peer-metric refresh.
- Readiness recalculation.
- Production health/freshness check.

Every workflow must:

- Validate required secrets without printing them.
- Use bounded batches and explicit timeouts.
- Save durable checkpoints.
- Be idempotent.
- Retry transient failures with limits.
- Summarize per-security failures.
- Avoid high-volume logging.
- Use concurrency guards.
- Support manual dispatch.
- Fail clearly when required production configuration is absent.

Do not hardcode `DATABASE_URL` or any credential. External configuration will supply secrets after code review.

### Gate

- CI validates workflow syntax and safety.
- Three consecutive production scheduled runs succeed after deployment.
- Failures create compact, actionable summaries.

## Phase 3 — Complete NIFTY 50 corpus

### Objective

Make a smaller, honest launch universe fully research-ready before attempting all 2,302 securities.

### Required datasets per security

- Exact security and provider mapping.
- Official complete classification.
- Listing-aware EOD market history.
- Current benchmark context.
- Quarterly and annual financial statements.
- Source-linked normalized financial facts.
- Recent filings.
- Recent results and earnings documents.
- Corporate actions and governance evidence.
- Searchable evidence chunks.
- Auditable peer and derived metrics.

### IDE work

- Improve market refresh for missing/renamed/unsupported symbols.
- Implement official classification ingestion with source ID and checksum.
- Implement resumable financial-statement ingestion.
- Implement filing discovery, download, parsing, deduplication and evidence linking.
- Store original large documents in private object storage through a server-side adapter.
- Build deterministic peer and financial metrics with formula versions.
- Trigger readiness recalculation after successful ingestion.
- Add an explicit supported-universe query derived from readiness, not a hardcoded green list.

### Gate

Every advertised NIFTY 50 stock passes the complete per-security contract. Any failing stock remains visible as preparing/incomplete and cannot be advertised as ready.

## Phase 4 — Durable research execution

### Objective

Turn the existing 16-role design into a proven production workflow.

### IDE work

- Verify queue creation and anonymous ownership.
- Implement safe worker leases.
- Prevent duplicate job execution.
- Persist agent-level status and errors.
- Add bounded retries and restart recovery.
- Preserve intermediate internal state.
- Never publish an incomplete report as complete.
- Enforce dependency ordering.
- Run independent agents in parallel only when safe.
- Run Evidence Cross-Validation before Chief Analyst synthesis.
- Store agent runs, claims, claim-evidence links, calculations and reports.
- Provide deterministic structured report generation when optional LLM enrichment is unavailable.
- Label unavailable enrichment honestly.
- Add provider quotas, timeouts and kill switches.

### Gate

Representative genuine jobs for multiple NIFTY 50 securities complete end-to-end without manual database edits.

## Phase 5 — Production report and frontend experience

### Objective

Make the public workflow understandable, responsive and honest.

### IDE work

- Show exact security resolution and exchange.
- Show per-stock readiness before enabling analysis.
- Replace indefinite “checking” states with timestamped system status.
- Show genuine durable job progress.
- Surface precise blocker and failure reasons.
- Render source-linked reports with as-of dates.
- Include thesis/counter-thesis, financials, peers, valuation scenarios, technical context, macro exposure, catalysts, risks, disconfirming evidence, confidence and limitations.
- Add Markdown, JSON and PDF export while preserving evidence and disclaimers.
- Verify research history and watchlists use anonymous ownership.
- Make the experience responsive on mobile and desktop.

### Gate

A new anonymous visitor can analyze a ready stock, observe progress, receive a report, reopen only their own report and export it.

## Phase 6 — Security, licensing and observability

### Objective

Finish the controls required for a public financial research application.

### IDE work

- Restrict CORS through configuration.
- Add validation, rate limits, daily quotas and concurrency limits.
- Add anonymous-session expiry and retention behavior.
- Test cross-user isolation.
- Add structured logging and correlation IDs.
- Add Sentry integration for frontend, API and workers without leaking sensitive values.
- Add alerts/health output for stale data, queue age, failed jobs, provider errors and storage pressure.
- Remove temporary monitoring demonstration routes after verification.
- Enforce commercial-source approval checks for public display.

### Gate

Deliberate frontend, API, worker and ingestion failures are captured; two-user security tests pass; restricted data is not presented as approved.

## Phase 7 — Production acceptance

### Required verification

- Search and entity resolution.
- Per-security readiness.
- Durable enqueue and job progress.
- Complete source-linked report.
- History, watchlists and exports.
- Latest completed EOD session handling.
- Latest available financial period.
- Filing and evidence freshness.
- Correct Asia/Kolkata display.
- Reproducible deterministic calculations.
- Anonymous isolation and RLS.
- Provider outage behavior.
- Worker restart and duplicate request handling.
- Database reconnect behavior.
- Load and latency targets.
- Backup, restore and rollback procedure.

### Gate

At least ten representative NIFTY 50 reports complete successfully and all critical acceptance checks are green.

## Phase 8 — Progressive expansion

Expand only after the prior batch passes:

1. NIFTY 50.
2. NIFTY Next 50.
3. NIFTY 200.
4. NIFTY 500.
5. Remaining liquid NSE EQ securities.
6. Remaining eligible NSE EQ securities.
7. Eligible BSE-only securities.

Do not claim full NSE/BSE support until the advertised universe satisfies the same contract.

## First IDE implementation batch

Unless a fresh audit proves a different blocker, begin with this bounded batch:

1. Inspect `PROJECT_INTENT.md`, readiness code, database engine creation and current migrations.
2. Add the stored per-security readiness migration.
3. Implement incremental readiness calculation without changing contract strictness.
4. Reuse the async database engine/pool.
5. Replace expensive readiness request paths with stored/cached reads.
6. Add focused migration, readiness, isolation and performance tests.
7. Improve workflow configuration validation and error summaries only as needed for this batch.
8. Run the relevant API test suite, compilation/type checks and workflow safety tests.
9. Commit on a dedicated branch.
10. Stop and provide the handoff report. Do not apply the migration or deploy.

## Mandatory handoff report

At the end of every IDE batch, report:

- Branch name.
- Commit SHA.
- Files changed.
- Database migrations added.
- Behavioral changes.
- Tests and exact commands run.
- Test results.
- Any tests not run and why.
- Environment variables or secrets required.
- External Supabase/Railway/Vercel/GitHub actions required.
- Known risks.
- Remaining blockers.
- Rollback considerations.
- Whether the change is safe to review, but do not claim it is production-ready before live verification.

## Prohibited actions for IDE agents

Do not:

- Push directly to `main`.
- Merge pull requests.
- Apply production database migrations.
- Change Railway, Vercel, Supabase, GitHub or Sentry settings.
- Delete production data or services.
- Create paid resources.
- Commit or print credentials.
- Disable readiness checks to make analysis succeed.
- Insert synthetic production data.
- relabel delayed EOD data as real-time.
- claim an agent works merely because its class or route exists.
- claim production readiness from CI alone.
- silently broaden scope beyond the active phase.

If blocked by missing credentials or external configuration, implement and test the code path, document the exact external requirement and stop safely.

## Definition of success

The project is production-ready only when its advertised securities can be analyzed end-to-end with current approved data, durable execution, evidence-linked validated reports, clear limitations, isolated anonymous ownership, acceptable performance, monitored operations and tested recovery.

A green deployment is not enough. A green CI run is not enough. A populated market-history table is not enough. Success is a complete, reproducible, safe user journey from public search through validated report and later retrieval.
