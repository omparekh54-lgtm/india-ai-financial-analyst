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

The API distinguishes liveness from readiness:

- `GET /health` checks whether the process is alive and reports basic database/configuration state.
- `GET /ready` is the traffic-admission check. It audits feature dependencies and database reachability and returns HTTP `503` when the runtime is unsafe to serve production traffic.

When `APP_ENV=production`, critical configuration errors also fail application startup. Examples include:

- missing `DATABASE_URL` or Supabase Auth configuration,
- non-HTTPS production web/CORS URLs,
- external LLM calls enabled without any provider key,
- Gemini multimodal/audio features enabled without Gemini + the external-LLM switch,
- semantic retrieval enabled without the Sentence-Transformers runtime,
- live market enabled without Upstox OAuth credentials and a valid Fernet broker-token encryption key,
- product telemetry enabled without a PostHog key.

Warnings such as a missing Sentry DSN do not block startup but appear in `/ready` so production gaps remain visible.

Configure the API host/load balancer to use `/ready` for readiness and `/health` only for liveness. Do not weaken the readiness checks to make a deployment turn green; fix the missing production configuration instead.

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

Page-level Gemini visual analysis is a separate opt-in capability. It analyzes only visually rich pages from selected filing types such as financial results, investor presentations and annual reports.

```text
ENABLE_MULTIMODAL_DOCUMENT_ANALYSIS=false
MULTIMODAL_MAX_PAGES_PER_DOCUMENT=4
MULTIMODAL_MAX_INLINE_BYTES=12000000
```

It additionally requires `ENABLE_EXTERNAL_LLM_CALLS=true` and a configured Gemini key. Visual findings are stored as `ai_extraction` evidence with exact filing page numbers. They are deliberately excluded from primary pgvector filing ranking and cannot independently become `verified` claims in Agent 15.

Turn this on only after ordinary text/XBRL filing ingestion is healthy and after testing Gemini quota/latency on a small filing sample.

## Earnings-call audio transcription

Audio transcription is separately opt-in:

```text
ENABLE_AUDIO_TRANSCRIPTION=false
AUDIO_TRANSCRIPTION_MAX_INLINE_BYTES=12000000
AUDIO_TRANSCRIPTION_MAX_OUTPUT_TOKENS=16000
AUDIO_TRANSCRIPT_CHUNK_CHARS=3200
```

It requires `ENABLE_EXTERNAL_LLM_CALLS=true` and a configured Gemini key. Raw audio bytes are not retained in the evidence database; only source provenance and timestamp-aware transcript chunks are persisted. Oversized audio is rejected instead of silently truncated. Transcript evidence can support management-commentary claims but cannot independently verify primary numeric filing facts.

Enable only after validating one small, known earnings-call sample and confirming quota/latency behavior.

## Supabase Auth

The browser signs in with the publishable key and sends the resulting access token to FastAPI as `Authorization: Bearer <token>`. FastAPI verifies the token against Supabase Auth and persists the Supabase user UUID in `research_jobs.requested_by`.

Configure the Supabase Auth **Site URL** to the production web origin and add preview/local redirect URLs deliberately. Do not use wildcard redirects broader than required for the environments you control.

The user-facing RLS policies are read-only and ownership-scoped. Raw ingestion/source tables remain backend-only.

## Container commands

Build the API image:

```bash
docker build -t india-ai-financial-analyst-api ./apps/api
```

Run API:

```bash
docker run --env-file .env -p 8000:8000 india-ai-financial-analyst-api
```

Run official feed worker from the same image:

```bash
docker run --env-file .env \
  india-ai-financial-analyst-api \
  python scripts/run_official_feed_daemon.py
```

Run live-market worker only after broker OAuth/live data is configured:

```bash
docker run --env-file .env \
  india-ai-financial-analyst-api \
  python scripts/run_live_market_worker.py
```

`deploy/docker-compose.production.yml` expresses the API/worker process split.

## Go-live order

1. Apply all database migrations.
2. Configure Supabase Auth URLs and email settings.
3. Deploy API with every external-call and optional-intelligence switch **off** and confirm `/health` is alive and `/ready` returns HTTP 200.
4. Deploy web with API + Supabase public variables and test account creation/sign-in.
5. Verify one authenticated research job is written with the correct `requested_by` UUID and is invisible to a second account.
6. Start the official-feed worker with external data enabled only after approved/licensed feed records are registered.
7. Enable semantic retrieval and backfill filing embeddings; verify fallback behavior and query latency.
8. Enable Tavily and LLM providers one at a time; verify provenance, fallback and quota usage.
9. Enable multimodal filing analysis on a small filing sample and verify that its evidence remains `ai_extraction` rather than primary evidence.
10. Enable audio transcription on a small known earnings-call sample and verify transcript evidence remains non-primary.
11. Configure broker OAuth/live-market adapters last; never store user broker tokens in the frontend bundle.
12. Enable PostHog/Sentry only after confirming privacy and secret configuration.
13. Run load, failure, security and data-freshness tests before public launch.
