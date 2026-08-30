# India AI Financial Analyst

India-first multimodal 16-agent equity research and market intelligence platform.

## Status

Foundation build in progress.

## Product principles

- India-first coverage for NSE/BSE equities.
- Evidence-first research with source provenance and validation.
- Deterministic Python for financial, valuation, forensic and technical calculations.
- Multimodal processing for filings, tables, charts, presentations and transcripts.
- Provider-agnostic LLM routing across Groq, Gemini, NVIDIA and Cerebras.
- Live-market adapters designed for FYERS / Angel One / Upstox without hard-coding one broker.
- No secrets committed to source control.

## Planned architecture

- `apps/web` — Next.js research dashboard
- `apps/api` — FastAPI application
- `backend/agents` — 16 logical agents
- `backend/connectors` — Indian market, filings, macro and web connectors
- `backend/providers` — LLM provider router
- `backend/calculations` — deterministic finance engines
- `database` — schema and migrations
- `tests` — unit/integration/agent regression tests

> Research intelligence only. Any future regulated recommendation workflow must be compliance-reviewed before commercial launch.
