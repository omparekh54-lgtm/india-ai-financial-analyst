# Production deployment runbook

The production system is intentionally split into three runtime surfaces:

1. **Web** — `apps/web`, deployed to Vercel or another Next.js host.
2. **API** — `apps/api`, deployed from the API Dockerfile as a long-lived web service.
3. **Official-feed worker** — the same API image, running `python scripts/run_official_feed_daemon.py` as a separate process.

Supabase provides PostgreSQL, pgvector and Auth. The worker must never run inside the browser/frontend deployment.

## Web environment

Set these on the Next.js deployment only:

```text
NEXT_PUBLIC_API_BASE_URL=https://<api-host>
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=<publishable-key>
```

Do not place database URLs, service-role keys, LLM keys or broker secrets in the web environment.

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
```

Add provider secrets only to the API/worker secret store. Enable each kill switch only after the corresponding credentials and data-quality checks are ready.

## Worker environment

Use the same server-side secret set as the API. The official-feed worker additionally uses:

```text
OFFICIAL_FEED_POLL_SECONDS=60
OFFICIAL_FEED_BATCH_SIZE=4
```

`ENABLE_EXTERNAL_DATA_CALLS` must be `true` before the worker will perform public-source network calls. Feed claims use database leases and ETag/Last-Modified checkpoints so multiple worker replicas do not intentionally process the same due feed at once.

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

For a two-process host, `deploy/docker-compose.production.yml` expresses the same split.

## Go-live order

1. Apply all database migrations.
2. Configure Supabase Auth URLs and email settings.
3. Deploy API with all external-call kill switches **off** and confirm `/health` reports database/auth configured.
4. Deploy web with API + Supabase public variables and test account creation/sign-in.
5. Verify one authenticated research job is written with the correct `requested_by` UUID and is invisible to a second account.
6. Start the official-feed worker with external data enabled only after approved feed records are registered.
7. Enable LLM/data providers one at a time and validate provenance/quotas.
8. Configure broker OAuth/live-market adapters last; never store user broker tokens in the frontend bundle.
