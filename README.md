# India AI Financial Analyst

India-first multimodal 16-agent equity research and market intelligence platform.

## Status

Foundation v1 is operational in the feature branch with a FastAPI research engine, Next.js dashboard, Supabase/Postgres evidence store, official India-source ingestion, sector-aware financial analysis, validation gating and Supabase user authentication.

## Product principles

- India-first coverage for NSE/BSE equities.
- Evidence-first research with source provenance and validation.
- Deterministic Python for financial, valuation, forensic and technical calculations.
- Multimodal processing for filings, tables, charts, presentations and transcripts.
- Provider-agnostic LLM routing across Groq, Gemini, NVIDIA and Cerebras.
- Live-market adapters designed for FYERS / Angel One / Upstox without hard-coding one broker.
- Authenticated research jobs and reports are isolated per Supabase user.
- No secrets committed to source control.

## Repository layout

- `apps/web` — Next.js authenticated research dashboard
- `apps/api` — FastAPI application, agents, orchestration, connectors and deterministic calculations
- `database/migrations` — PostgreSQL/Supabase schema, RLS and ingestion migrations
- `apps/api/tests` — unit, parser, agent and regression tests

## Authentication and ownership

The browser authenticates with Supabase using only `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`. The access token is sent to FastAPI as a Bearer token. FastAPI validates it against Supabase Auth and stores the authenticated UUID in `research_jobs.requested_by`.

User-readable tables have RLS policies tied to `auth.uid()`: `research_jobs`, `agent_runs`, `claims`, `claim_evidence`, `research_reports` and `analysis_snapshots`. Shared raw ingestion tables such as `sources`, `evidence_chunks`, official feed registries and market/macro storage remain backend-only. The API also filters job/report reads by `requested_by` even though its trusted database role can bypass RLS.

The public `POST /v1/research/run` contract accepts only a company/ticker query and analysis mode. Arbitrary client-supplied evidence or financial context is rejected; trusted internal code can still pass structured context directly to `ResearchService` for tests and controlled workflows.

## Environment separation

Server-only values include `DATABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and all provider/broker API secrets. Never expose them through `NEXT_PUBLIC_*` variables. The browser receives only the Supabase project URL, publishable key and public API base URL.

See `.env.example` for the complete variable list. External LLM, external-data and live-market calls remain behind runtime kill switches until deployment secrets are configured.

> Research intelligence only. Any future regulated recommendation workflow must be compliance-reviewed before commercial launch.
