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
CORS_ORIGINS=https://<web-host>
ENABLE_EXTERNAL_LLM_CALLS=false
ENABLE_EXTERNAL_DATA_CALLS=false
ENABLE_LIVE_MARKET=false
ENABLE_SEMANTIC_RETRIEVAL=false
ENABLE_MULTIMODAL_DOCUMENT_ANALYSIS=false
```

Add provider secrets only to the API/worker secret store. Enable each feature only after the corresponding credentials, quota controls and data-quality checks are ready.

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
3. Deploy API with every external-call and optional-intelligence switch **off** and confirm `/health` reports database/auth configured.
4. Deploy web with API + Supabase public variables and test account creation/sign-in.
5. Verify one authenticated research job is written with the correct `requested_by` UUID and is invisible to a second account.
6. Start the official-feed worker with external data enabled only after approved/licensed feed records are registered.
7. Enable semantic retrieval and backfill filing embeddings; verify fallback behavior and query latency.
8. Enable Tavily and LLM providers one at a time; verify provenance, fallback and quota usage.
9. Enable multimodal filing analysis on a small filing sample and verify that its evidence remains `ai_extraction` rather than primary evidence.
10. Configure broker OAuth/live-market adapters last; never store user broker tokens in the frontend bundle.
11. Run load, failure, security and data-freshness tests before public launch.
