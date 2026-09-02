# Phases 47-50 - Deployment Cutover Readiness

This block captures the deployment setup completed after Phase 43-46: Railway backend runtime, Vercel preview-to-backend wiring, controlled production promotion and final live acceptance evidence. It is intentionally fail-closed and must not be used to claim that the real research corpus or commercial launch gate is complete.

Run locally or in CI with non-secret evidence:

```bash
python scripts/run_deployment_cutover_gate.py --plan-only
python scripts/run_deployment_cutover_gate.py --evidence-json deployment-cutover-evidence.json
```

The evidence JSON must contain only booleans, IDs, URLs, counts, statuses and approval references. It must not contain access tokens, provider keys, database URLs, passwords, client secrets or service-role credentials.

## Phase 47 - Railway backend runtime

Required evidence:

- Railway project ID and production environment ID.
- API service ID.
- API deployment status is `SUCCESS`.
- Required worker services are `SUCCESS`.
- Public Railway API domain exists and is HTTPS.
- API target port is `8000`.
- `GET /health` returns HTTP 200.
- `GET /ready` returns HTTP 200.
- `/ready` reports `database_healthy: true`.
- Runtime error count is zero.

Current verified setup: Railway API and all three workers are deployed successfully, and `https://api-production-d331d.up.railway.app/ready` returned HTTP 200 with `database_healthy: true`. The API reports a non-blocking warning while `SENTRY_DSN` is not configured.

## Phase 48 - Vercel frontend-backend wiring

Required evidence:

- Vercel team ID and project ID.
- GitHub repository link exactly matches `omparekh54-lgtm/india-ai-financial-analyst`.
- Latest preview deployment ID.
- Latest preview deployment is ready.
- Preview commit SHA is recorded.
- Branch alias is available.
- Vercel build detected `apps/web/.env.production`.
- `NEXT_PUBLIC_API_BASE_URL` points to the Railway API URL.
- Frontend runtime error count is zero.

Current verified setup: Vercel built commit `236e6ec84ebdb5fc8358a8063eb36068cfe4290b`, loaded `.env.production`, and produced a `READY` preview deployment.

## Phase 49 - Release promotion controls

Required evidence:

- Pull request number.
- Source branch.
- Target production branch.
- Latest preview commit SHA.
- Production branch matches the intended target.
- Required checks are green.
- Promotion to production is manual or otherwise explicitly approved.
- Rollback candidate exists.
- Commercial launch remains disabled until real corpus readiness passes.
- No service-role key is present in frontend config.

This phase ensures the preview deployment is not silently promoted without operator approval and rollback context.

## Phase 50 - Live acceptance evidence

Required evidence:

- Production URL.
- Production URL HTTPS verification.
- Post-promotion smoke is required.
- Phase 25-30 release gate status.
- Phase 31-36 post-launch gate status.
- Phase 43-46 activation gate status.
- Known blockers are recorded.
- Corpus is not marked complete without real data.
- Operator acceptance reference.

This phase is the handoff between deployment setup and real production acceptance. It may pass for deployment cutover only when it clearly records whether the release gates are passed or blocked by the known real-corpus/security prerequisites.
