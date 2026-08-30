# Production deployment runbook

The production system is intentionally split into four runtime surfaces:

1. **Web** — `apps/web`, deployed to Vercel or another Next.js host.
2. **API** — `apps/api`, deployed from the API Dockerfile as a long-lived web service.
3. **Official-feed worker** — the same API image, running `python scripts/run_official_feed_daemon.py` as a separate process.
4. **Live-market worker** — the same API image, running `python scripts/run_live_market_worker.py` only when broker live data is deliberately enabled.

Supabase provides PostgreSQL, pgvector and Auth. Background workers must never run inside the browser/frontend deployment.

## Web environment

Set these on the Next.js deployment only:

```text
NEXT_PUBLIC_API_BASE_URL=https://<api-host>
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<publishable-key>
```

Do not place database URLs, service-role keys, LLM keys, embedding configuration secrets or broker secrets in the web environment.

## API environment

At minimum:

```text
APP_ENV=production
DATABASE_URL=<supabase-postgres-connection>
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_PUBLISHABLE_KEY=<publishable-key>
WEB_APP_URL=https://<web-host>
CORS_ORIGINS=https://<web-host>
ENABLE_EXTERNAL_LLM_CALLS=false
ENABLE_EXTERNAL_DATA_CALLS=false
ENABLE_LIVE_MARKET=false
ENABLE_SEMANTIC_RETRIEVAL=false
ENABLE_MULTIMODAL_DOCUMENT_ANALYSIS=false
ENABLE_AUDIO_TRANSCRIPTION=false
ENABLE_PRODUCT_TELEMETRY=false
```

Add provider secrets only to the API/worker secret store. Enable each feature only after the corresponding credentials, quota controls and data-quality checks are ready.

## Production readiness and fail-closed startup

The API distinguishes liveness, runtime readiness and corpus readiness:

- `GET /health` checks whether the process is alive and reports basic database/configuration state.
- `GET /ready` is the traffic-admission check. It audits feature dependencies and database reachability and returns HTTP `503` when the runtime is unsafe to serve production traffic.
- `GET /v1/system/data-readiness` is authenticated and reports the research-corpus gate separately, including NSE-universe coverage, missing datasets and stale-data warnings.

When `APP_ENV=production`, critical configuration errors also fail application startup. Examples include missing database/Auth configuration, non-HTTPS production origins, enabled LLM features without their providers, semantic retrieval without its local runtime, or live market without encrypted broker OAuth configuration.

Warnings such as a missing Sentry DSN do not block startup but appear in `/ready`. Configure the API host/load balancer to use `/ready` for readiness and `/health` only for liveness. Do not weaken readiness checks to make a deployment green; fix the production configuration instead.

Before rollout, run the zero-external-call structural preflight:

```bash
python scripts/run_production_preflight.py
```

This verifies configuration, database connectivity, pgvector, the semantic HNSW index, required tables, research ownership, RLS enablement and required owner-read policies. It does not call an LLM, broker, exchange or market-data API.

After reference/data bootstrap, run the data coverage audit:

```bash
python scripts/run_data_coverage_audit.py
```

The data audit treats a full NSE EQ security universe as a hard production prerequisite and reports missing fundamentals, filings/evidence, market bars, benchmark bars, macro observations, peer metrics, embeddings and enabled official feeds explicitly. A structurally healthy but empty database must not be mistaken for a production-ready research dataset.

## NSE security-master bootstrap

Use only the official NSE security-master importer. Run a dry-run first:

```bash
python scripts/import_nse_security_master.py --dry-run
```

The importer validates the expected CSV header, rejects suspiciously small universes and duplicate symbol/ISIN identifiers, records a SHA-256 source checksum, and restricts remote downloads to official NSE hosts. After validating the dry-run row count/checksum, run the actual import with `DATABASE_URL` configured:

```bash
python scripts/import_nse_security_master.py
```

Do not substitute an unofficial mirror merely to make the coverage gate pass.

## Guarded one-command corpus bootstrap

`bootstrap_research_data.py` orchestrates the already-validated importers in deterministic order: NSE security master, approved benchmark CSVs, approved macro CSVs, optional explicitly enabled official feeds, optional evidence embeddings, then the canonical data-readiness audit.

Validate file inputs before writing anything:

```bash
python scripts/bootstrap_research_data.py \
  --nse-file /data/EQUITY_L.csv \
  --benchmark NIFTY50,nse,/data/nifty50.csv \
  --benchmark INDIAVIX,nse,/data/india_vix.csv \
  --macro-file /data/rbi_macro.csv \
  --dry-run
```

For the first write pass, remove `--dry-run` and add `--require-ready` when the deployment must fail unless the hard corpus gate is satisfied:

```bash
python scripts/bootstrap_research_data.py \
  --nse-file /data/EQUITY_L.csv \
  --benchmark NIFTY50,nse,/data/nifty50.csv \
  --benchmark INDIAVIX,nse,/data/india_vix.csv \
  --macro-file /data/rbi_macro.csv \
  --require-ready
```

If the NSE universe is already populated, resume later stages without re-downloading it:

```bash
python scripts/bootstrap_research_data.py \
  --skip-nse \
  --benchmark NIFTY50,nse,/data/nifty50.csv \
  --macro-file /data/rbi_macro.csv
```

The command is fail-fast and emits a machine-readable JSON summary with coverage before/after and the failed stage, if any. `--run-official-feeds` and `--embed-evidence` are explicit write-capable stages and cannot be combined with `--dry-run`. The bootstrap command never enables disabled feed templates; production feed activation remains a separate licensing/source-governance decision.

## Worker environment

Use the same server-side secret set as the API. The official-feed worker additionally uses:

```text
OFFICIAL_FEED_POLL_SECONDS=60
OFFICIAL_FEED_BATCH_SIZE=4
```

`ENABLE_EXTERNAL_DATA_CALLS` must be `true` before the worker performs approved public-source network calls. Feed claims use database leases and ETag/Last-Modified checkpoints so multiple worker replicas do not intentionally process the same due feed at once.

## Semantic evidence retrieval

The production image includes the optional local Sentence-Transformers runtime. No paid embedding API is required. The default model produces 384-dimensional vectors that match `evidence_chunks.embedding`.

Safe activation order:

1. Apply `0013_semantic_evidence.sql` so the HNSW cosine index exists.
2. Start the API with `ENABLE_SEMANTIC_RETRIEVAL=false` and verify ordinary research still works.
3. Enable semantic retrieval on one backend instance and confirm the local embedding model loads successfully.
4. Backfill existing filing chunks in bounded batches:

```bash
ENABLE_SEMANTIC_RETRIEVAL=true \
python scripts/backfill_evidence_embeddings.py --batch-size 64
```

5. Verify embedded row counts and query latency before enabling it across all API instances.

Embedding/model failure is non-fatal: research falls back to recent filing chunks rather than failing the job.

## Fresh web/news research

Tavily acquisition is cache-first. Default safeguards are:

```text
WEB_RESEARCH_CACHE_SECONDS=900
WEB_RESEARCH_MAX_SEARCHES_PER_JOB=2
WEB_RESEARCH_MAX_RESULTS_PER_SEARCH=5
```

Repeated research reuses recent `web_search` rows. `why_did_it_move` uses a shorter cache window but the same bounded search count. Enable `ENABLE_EXTERNAL_DATA_CALLS` only after the Tavily secret is configured and monitor credits before changing these limits.

## Multimodal filing analysis

Page-level Gemini visual analysis is separately opt-in:

```text
ENABLE_MULTIMODAL_DOCUMENT_ANALYSIS=false
MULTIMODAL_MAX_PAGES_PER_DOCUMENT=4
MULTIMODAL_MAX_INLINE_BYTES=12000000
```

It additionally requires `ENABLE_EXTERNAL_LLM_CALLS=true` and a configured Gemini key. Visual findings are stored as `ai_extraction` evidence with exact filing page numbers. They are excluded from primary pgvector filing ranking and cannot independently become `verified` claims in Agent 15.

## Earnings-call audio transcription

Audio transcription is separately opt-in:

```text
ENABLE_AUDIO_TRANSCRIPTION=false
AUDIO_TRANSCRIPTION_MAX_INLINE_BYTES=12000000
AUDIO_TRANSCRIPTION_MAX_OUTPUT_TOKENS=16000
AUDIO_TRANSCRIPT_CHUNK_CHARS=3200
```

It requires `ENABLE_EXTERNAL_LLM_CALLS=true` and a configured Gemini key. Raw audio bytes are not retained in the evidence database; only source provenance and timestamp-aware transcript chunks are persisted. Oversized audio is rejected instead of silently truncated. Transcript evidence can support management-commentary claims but cannot independently verify primary numeric filing facts.

## Supabase Auth

The browser signs in with the publishable key and sends the resulting access token to FastAPI as `Authorization: Bearer <token>`. FastAPI verifies the token against Supabase Auth and persists the Supabase user UUID in `research_jobs.requested_by`.

Configure the Supabase Auth **Site URL** to the production web origin and add preview/local redirect URLs deliberately. Do not use wildcard redirects broader than required for the environments you control. User-facing RLS policies are read-only and ownership-scoped; raw ingestion/source tables remain backend-only.

## Container commands

Build and run the API:

```bash
docker build -t india-ai-financial-analyst-api ./apps/api
docker run --env-file .env -p 8000:8000 india-ai-financial-analyst-api
```

Run workers from the same image:

```bash
docker run --env-file .env india-ai-financial-analyst-api python scripts/run_official_feed_daemon.py
docker run --env-file .env india-ai-financial-analyst-api python scripts/run_live_market_worker.py
```

The API image healthcheck uses `/ready`; worker HTTP healthchecks are disabled because workers do not serve port 8000. `deploy/docker-compose.production.yml` expresses the process split.

## Go-live order

1. Apply all database migrations and run `python scripts/run_production_preflight.py`.
2. Configure Supabase Auth URLs/email settings.
3. Dry-run `bootstrap_research_data.py` against the official NSE security master plus approved benchmark/macro files, inspect checksums/counts, then execute the write pass.
4. Bootstrap approved fundamentals and filing/evidence sources, then rerun `python scripts/run_data_coverage_audit.py` and check `/v1/system/data-readiness` from an authenticated session.
5. Deploy API with every external-call/optional-intelligence switch **off**; verify `/health` and `/ready`.
6. Deploy web and test account creation/sign-in; verify one user's research is invisible to a second account.
7. Start approved/licensed official-data ingestion only after the production data-source decision is complete.
8. Enable semantic retrieval/backfill, then Tavily and LLM providers one at a time; verify provenance, fallback and quotas.
9. Enable multimodal filing analysis and audio transcription on small known samples; verify their evidence remains non-primary.
10. Configure broker OAuth/live-market last; never expose broker tokens or secrets to the frontend.
11. Enable PostHog/Sentry after privacy and secret configuration are confirmed.
12. Run load, provider-failure, security, auth-isolation and data-freshness tests before public launch.
