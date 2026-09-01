# Production QA workflow

This workflow validates a deployed India AI Financial Analyst instance without creating synthetic research data, mutating the research corpus during QA, or spending LLM/search-provider quota unless the operator explicitly enables those integrations.

## 1. Structural preflight

Run from the API image/environment before traffic is admitted:

```bash
python scripts/run_production_preflight.py
```

This checks production configuration, PostgreSQL connectivity, pgvector, semantic-index requirements, required tables, research ownership/RLS controls and source-governance constraints. It does not call market-data, broker, search, exchange, or LLM providers.

## 2. Build the real production research corpus

The production corpus controller runs existing real-data workers in fail-closed order:

```bash
python scripts/bootstrap_production_research_corpus.py --plan-only \
  --repo-source-url '<official RBI repo export>' \
  --cpi-source-url '<official RBI CPI export>' \
  --iip-source-url '<official RBI IIP export>'

python scripts/bootstrap_production_research_corpus.py \
  --repo-source-url '<official RBI repo export>' \
  --cpi-source-url '<official RBI CPI export>' \
  --iip-source-url '<official RBI IIP export>'
```

Stages are:

1. official NSE universe/classification + exact provider mappings + complete India market/macro context + listing-aware market history,
2. official NSE financial-result XBRL + deterministic financial facts + filing/earnings evidence,
3. deterministic peer/security metrics derived only from source-linked inputs,
4. the authoritative 16-agent readiness gate.

There is no synthetic, estimated, placeholder, or silent paid-provider fallback.

### Manual protected corpus workflow

Operators can use `.github/workflows/production-corpus.yml` after the protected `production` environment is configured. It is `workflow_dispatch` only, requires `RUN_REAL_CORPUS`, uses read-only GitHub repository permission, prevents overlapping corpus runs, keeps credentials in Actions/environment secrets, and runs with `FREE_ONLY=true`.

## 3. Corpus manifest and 16-agent readiness

Use the Phase 25 read-only manifest after corpus ingestion:

```bash
python scripts/run_production_corpus_manifest.py --min-nse-eq-securities 1000
```

It returns corpus coverage, authoritative 16-agent readiness, blocking agents and concrete real-data next actions. It never mutates data.

The lower-level readiness and diagnostic commands remain available:

```bash
python scripts/run_agent_readiness_gate.py
python scripts/run_agent_coverage_gap_report.py
```

Listing-age-aware financial and market policies remain authoritative; do not replace them with universal eight-period or 200-bar checks for recent listings.

## 4. Representative real-company acceptance

A production release must prove that persisted real research has passed through the evidence architecture:

```bash
export REAL_COMPANY_ACCEPTANCE_JOB_IDS='<job-1>,<job-2>,<job-3>,<job-4>,<job-5>'
python scripts/run_real_company_acceptance.py
```

By default the gate requires at least five distinct real securities across four populated sectors. Every supplied job must be completed, resolve to a real security, contain a persisted report, have completed Agent 15 and Agent 16 runs, contain validated claims, and contain claim-to-evidence/source links. Non-production provenance markers fail the gate.

The gate does not generate research jobs or acceptance fixtures.

## 5. Provider activation policy

Validate optional integration flags, credential presence and `FREE_ONLY` routing:

```bash
python scripts/run_provider_activation_gate.py
```

Enabled integrations fail closed when required credentials or a FREE_ONLY-compatible route are unavailable. The output is configuration/policy verification only; it does not pretend that credential presence proves endpoint health, quota, licensing or a live broker session.

## 6. Deployment package and authenticated smoke

The checked-in deployment package is `deploy/docker-compose.production.yml`. It contains the API, research worker, official-feed worker and live-market worker, plus a maintenance-only one-shot calibration service. The API has a container healthcheck and long-running services use init/graceful-stop handling.

Run calibration explicitly or from an approved scheduler:

```bash
docker compose -f deploy/docker-compose.production.yml --profile maintenance run --rm research-calibration
```

For deployed API smoke, keep the token environment-only:

```bash
export API_BASE_URL=https://api.example.com
export DEPLOYMENT_SMOKE_ACCESS_TOKEN='<short-lived Supabase access token>'
python scripts/run_deployment_smoke.py --require-corpus-ready
```

The smoke command performs GET requests only and checks `/health`, `/ready`, all 16 registered roles, authenticated identity, authenticated corpus readiness, zero non-production sources and zero enabled unapproved official feeds. Remote targets must use HTTPS.

## 7. Operations, commercial approval, isolation and load

Read-only operations health:

```bash
python scripts/run_operations_health_report.py
```

Commercial/source approval gate:

```bash
python scripts/run_commercial_launch_gate.py
```

The commercial gate remains red unless production configuration, operations/corpus health and every configured user-display source scope have explicit active approval references. It never infers licensing permission.

Two-user ownership isolation uses an existing real research job owned by one test user:

```bash
export OWNER_ACCESS_TOKEN='<owner token>'
export OTHER_ACCESS_TOKEN='<different user token>'
python scripts/verify_auth_isolation.py \
  --api-base-url https://api.example.com \
  --job-id '<existing owner research job UUID>'
```

The bounded read-only load probe permits at most 500 requests and concurrency 25:

```bash
python scripts/run_load_probe.py \
  --api-base-url https://api.example.com \
  --requests 100 \
  --concurrency 10
```

Only a fixed allow-list of GET endpoints is permitted. Research creation and other mutating/provider-backed routes cannot be selected.

## 8. Final one-command production release gate

After a real corpus exists and the API is deployed:

```bash
export API_BASE_URL=https://api.example.com
export AUTH_ISOLATION_JOB_ID='<existing owner research job UUID>'
export REAL_COMPANY_ACCEPTANCE_JOB_IDS='<representative completed real job UUIDs>'
export DEPLOYMENT_SMOKE_ACCESS_TOKEN='<smoke user token>'
export OWNER_ACCESS_TOKEN='<owner token>'
export OTHER_ACCESS_TOKEN='<different user token>'
python scripts/run_production_release_gate.py
```

Preview requirements without executing stages:

```bash
python scripts/run_production_release_gate.py --plan-only
```

The Phase 30 gate is fixed and fail-closed. It runs, in order:

1. structural production preflight,
2. production corpus + all 16 agent readiness,
3. representative real-company end-to-end acceptance,
4. provider/integration activation policy,
5. operations health,
6. commercial/source approval,
7. authenticated deployed API smoke,
8. two-user ownership isolation,
9. bounded GET-only load probe.

A failure in any stage prevents `release_ready=true`. Access tokens and provider credentials are environment-only and are never CLI arguments.

### Manual protected release workflow

The preferred production execution path is `.github/workflows/production-release-gate.yml`.

It is `workflow_dispatch` only, requires exact confirmation text `RUN_RELEASE_GATE`, uses read-only repository permission, prevents overlapping release-gate runs, requires HTTPS deployment inputs and representative real-company job IDs, and obtains access/provider credentials only from the protected GitHub Environment.

`COMMERCIAL_LAUNCH_ENABLED=true` is set only inside this explicitly invoked release workflow. Optional external LLM/data/live-market integrations remain disabled unless the operator turns on their workflow inputs; when enabled, their required secrets are validated before the gate runs.

The workflow does not create research jobs or mutate the research corpus. It uploads the structured release result as a short-retention artifact.

## 9. CI production configuration check

Pull-request CI validates three independent surfaces:

1. API Ruff + mypy + pytest, including release controls and production-workflow safety regression tests.
2. Next.js production build.
3. `docker compose -f deploy/docker-compose.production.yml config` and the API Dockerfile.

This catches malformed application/process definitions without starting workers or calling external providers.

## 10. Supabase security/performance review

After DDL changes and before launch, run the Supabase security and performance advisors. Resolve actionable security defects and unindexed foreign-key findings. Do not delete freshly created indexes merely because an empty/new table reports an INFO-level `unused_index` notice before production workload exists.

## 11. What this QA deliberately does not fake

These checks do not fabricate securities, prices, financial facts, filings, macro observations, benchmarks, peer metrics, transcripts, evidence, source approvals, representative research jobs or provider success.

A green software CI run is not the same thing as a green live corpus gate, and neither is the same thing as a completed commercial release gate. Genuine corpus population, representative real-company evidence, source/licensing approvals, production credentials for intentionally enabled integrations and an actual HTTPS deployment remain external go-live prerequisites.

## 12. Deployment readiness Phase 37-42

Before creating or promoting a Vercel production deployment, collect non-secret deployment evidence and run:

```bash
python scripts/run_deployment_readiness_gate.py --plan-only
python scripts/run_deployment_readiness_gate.py --evidence-json deployment-readiness-evidence.json
```

The protected manual workflow is `.github/workflows/deployment-readiness.yml`. It requires `RUN_DEPLOYMENT_READINESS`, runs with read-only repository permission, rejects secret-like evidence keys, and uploads a structured deployment-readiness artifact.

This gate covers the Vercel project link, protected environment contract, Supabase migration/security state, exact build artifact commit, auth/traffic preparation and final release runbook. It should be green before Phase 30 is run against a live HTTPS deployment.

## 13. Post-launch Phase 31-36 acceptance

After Phase 30 is genuinely green, collect non-secret production evidence and run:

```bash
python scripts/run_post_launch_acceptance_gate.py --plan-only
python scripts/run_post_launch_acceptance_gate.py --evidence-json post-launch-evidence.json
```

The protected manual workflow is `.github/workflows/post-launch-acceptance.yml`. It requires `RUN_POST_LAUNCH_GATE`, runs with read-only repository permission, rejects secret-like evidence keys, and uploads a structured acceptance artifact.

For the detailed Phase 25-30 contract, see `docs/PHASE_25_30_RELEASE_ACCEPTANCE.md`.
For the detailed Phase 31-36 contract, see `docs/PHASE_31_36_POST_LAUNCH_ACCEPTANCE.md`.
For the detailed Phase 37-42 contract, see `docs/PHASE_37_42_DEPLOYMENT_READINESS.md`.
