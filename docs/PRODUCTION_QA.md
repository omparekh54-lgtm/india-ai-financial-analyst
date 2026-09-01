# Production QA workflow

This workflow validates a deployed India AI Financial Analyst instance without creating synthetic research data, mutating the research corpus during QA, or spending LLM/search-provider quota by default.

## 1. Structural preflight

Run from the API image/environment before traffic is admitted:

```bash
python scripts/run_production_preflight.py
```

This checks production configuration, PostgreSQL connectivity, pgvector, the semantic index, required tables, research ownership/RLS policies, private watchlist tables/RLS policies, and the reference-source approval constraint. It does not call market-data, broker, search, exchange, or LLM providers.

## 2. Build the real production research corpus

The production corpus controller runs existing real-data workers in fail-closed order:

```bash
python scripts/bootstrap_production_research_corpus.py --plan-only
python scripts/bootstrap_production_research_corpus.py
```

Stages are:

1. official NSE universe/classification + exact provider mappings + India market context + listing-aware market history,
2. official NSE financial-result XBRL + deterministic financial facts + filing/earnings evidence,
3. deterministic peer/security metrics derived only from source-linked inputs,
4. the authoritative 16-agent readiness gate.

The controller can resume at a later stage, for example:

```bash
python scripts/bootstrap_production_research_corpus.py --start-at financials
```

There is no synthetic, estimated, placeholder, or silent paid-provider fallback.

## 3. Authoritative corpus and agent readiness

Use the read-only gate after corpus ingestion:

```bash
python scripts/run_agent_readiness_gate.py
```

This command exits non-zero unless both the corpus contract and all 16 agent data contracts pass. Listing-age-aware financial and market policies are authoritative; do not replace them with universal eight-period or 200-bar checks for recent listings.

For diagnostics without mutation:

```bash
python scripts/run_agent_coverage_gap_report.py
```

The gap report uses the same authoritative financial-history, market-history, peer-metric, provenance, benchmark, macro and agent-readiness policies as the release gate.

## 4. Authenticated deployment smoke

Set the token only through the environment so it does not appear in shell history:

```bash
export API_BASE_URL=https://api.example.com
export DEPLOYMENT_SMOKE_ACCESS_TOKEN='<short-lived Supabase access token>'
python scripts/run_deployment_smoke.py --require-corpus-ready
```

The smoke command performs GET requests only. It checks:

- `/health`
- `/ready`
- all 16 registered research roles
- authenticated identity
- authenticated corpus readiness
- zero detected non-production sources
- zero enabled unapproved official feeds

Authenticated smoke checks require HTTPS for remote hosts. Plain HTTP is accepted only for localhost/loopback development targets. URLs containing embedded credentials are rejected.

## 5. Two-user ownership isolation

Use an existing real research job owned by one test user. Tokens remain environment-only:

```bash
export OWNER_ACCESS_TOKEN='<owner token>'
export OTHER_ACCESS_TOKEN='<different user token>'
python scripts/verify_auth_isolation.py \
  --api-base-url https://api.example.com \
  --job-id '<existing owner research job UUID>'
```

The check is read-only. It verifies the owner can read the job while a different authenticated user cannot read the same private research resource.

## 6. Bounded read-only load probe

The default load probe only targets health, readiness, and agent-registry GET endpoints:

```bash
python scripts/run_load_probe.py \
  --api-base-url https://api.example.com \
  --requests 100 \
  --concurrency 10
```

Safety limits are enforced in code: at most 500 requests and concurrency 25. Only a fixed allow-list of GET endpoints is permitted; research creation and all other mutating/provider-backed routes are impossible to select.

To include authenticated read-only checks, provide the token through the environment:

```bash
export LOAD_PROBE_ACCESS_TOKEN='<short-lived Supabase access token>'
python scripts/run_load_probe.py \
  --api-base-url https://api.example.com \
  --endpoint /v1/auth/me \
  --endpoint /v1/system/data-readiness \
  --requests 100 \
  --concurrency 10
```

The output reports success/failure counts, HTTP status counts, endpoint counts, network error types, and p50/p95/max latency. Authenticated probes require HTTPS outside localhost and never put the bearer token in the URL.

## 7. One-command production release gate

After a real corpus exists and a deployed API is available, run the aggregate gate:

```bash
export API_BASE_URL=https://api.example.com
export AUTH_ISOLATION_JOB_ID='<existing owner research job UUID>'
export DEPLOYMENT_SMOKE_ACCESS_TOKEN='<smoke user token>'
export OWNER_ACCESS_TOKEN='<owner token>'
export OTHER_ACCESS_TOKEN='<different user token>'
python scripts/run_production_release_gate.py
```

Preview the required stages without secrets:

```bash
python scripts/run_production_release_gate.py --plan-only
```

The release gate is deliberately fail-closed and runs, in order:

1. structural production preflight,
2. authoritative 16-agent corpus readiness,
3. authenticated GET-only deployment smoke with corpus readiness required,
4. two-user ownership isolation,
5. bounded GET-only load probe.

A failure in any stage prevents `release_ready=true`. Access tokens are never accepted as CLI arguments.

## 8. CI production configuration check

Pull-request CI validates three independent surfaces:

1. API Ruff + mypy + pytest, including scripts.
2. Next.js production build, including the private watchlist workspace and evidence UI.
3. `docker compose -f deploy/docker-compose.production.yml config` and the API Dockerfile.

This catches malformed application/process definitions without starting workers or calling external providers.

## 9. What this QA deliberately does not do

These checks do not fabricate securities, prices, financial facts, filings, macro observations, benchmarks, peer metrics, transcripts, or evidence. They also do not automatically enable NSE/BSE development feed templates, Upstox live market, Tavily, Gemini, Groq, NVIDIA, Cerebras, or other external providers.

Real provider activation and real-data backfills are separate go-live steps and must retain their source/licensing/provenance records. A green software CI run is not the same thing as a green live corpus readiness gate.
