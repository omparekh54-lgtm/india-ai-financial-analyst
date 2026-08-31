# Production QA workflow

This workflow validates a deployed India AI Financial Analyst instance without creating synthetic research data, mutating the research corpus, or spending LLM/search-provider quota by default.

## 1. Structural preflight

Run from the API image/environment before traffic is admitted:

```bash
python scripts/run_production_preflight.py
```

This checks production configuration, PostgreSQL connectivity, pgvector, the semantic index, required tables, research ownership/RLS policies, and the reference-source approval constraint. It does not call market-data, broker, search, exchange, or LLM providers.

## 2. Corpus readiness

After importing only real provenance-backed data:

```bash
python scripts/run_data_coverage_audit.py
```

A production corpus fails if it contains detected synthetic/mock/fake/dummy/fixture/sample/generated/placeholder sources, unapproved enabled feeds, or material rows that are missing their required source provenance. Do not weaken this gate to make a deployment pass.

## 3. Authenticated deployment smoke

Set the token only through the environment so it does not appear in shell history:

```bash
export API_BASE_URL=https://api.example.com
export DEPLOYMENT_SMOKE_ACCESS_TOKEN='<short-lived Supabase access token>'
python scripts/run_deployment_smoke.py
```

The smoke command performs GET requests only. It checks:

- `/health`
- `/ready`
- all 16 registered research roles
- authenticated identity
- authenticated corpus readiness
- zero detected non-production sources
- zero enabled unapproved official feeds

To require a fully populated real-data corpus as part of the smoke result:

```bash
python scripts/run_deployment_smoke.py --require-corpus-ready
```

Authenticated smoke checks require HTTPS for remote hosts. Plain HTTP is accepted only for localhost/loopback development targets. URLs containing embedded credentials are rejected.

## 4. Bounded read-only load probe

The default load probe only targets health, readiness, and agent-registry GET endpoints:

```bash
python scripts/run_load_probe.py \
  --api-base-url https://api.example.com \
  --requests 100 \
  --concurrency 10
```

Safety limits are enforced in code: at most 500 requests and concurrency 25. Only a fixed allow-list of GET endpoints is permitted; `/v1/research/run` and all other mutating/provider-backed routes are impossible to select.

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

## 5. CI production configuration check

Pull-request CI validates three independent surfaces:

1. API Ruff + pytest, including scripts.
2. Next.js production build.
3. `docker compose -f deploy/docker-compose.production.yml config` using an empty CI-only environment file.

This catches malformed Compose/process definitions without starting workers or calling external providers.

## 6. What this QA deliberately does not do

These checks do not fabricate securities, prices, financial facts, filings, macro observations, benchmarks, peer metrics, transcripts, or evidence. They also do not automatically enable NSE/BSE development feed templates, Upstox live market, Tavily, Gemini, Groq, NVIDIA, Cerebras, or other external providers.

Real provider activation and real-data backfills are separate go-live steps and must retain their source/licensing/provenance records.
