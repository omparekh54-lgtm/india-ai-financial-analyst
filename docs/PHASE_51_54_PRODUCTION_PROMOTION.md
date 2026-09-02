# Phases 51-54 - Production Promotion Readiness

This block captures the final promotion controls after Railway and Vercel are connected: branch promotion readiness, production Vercel domain verification, backend post-promotion sync and the launch decision register. It is intentionally fail-closed and must not be used to claim that unresolved real-corpus, Supabase security, Sentry or npm-audit items are complete.

Run locally or in CI with non-secret evidence:

```bash
python scripts/run_production_promotion_gate.py --plan-only
python scripts/run_production_promotion_gate.py --evidence-json production-promotion-evidence.json
```

The evidence JSON must contain only booleans, IDs, URLs, counts, statuses and approval references. It must not contain access tokens, provider keys, database URLs, passwords, client secrets or service-role credentials.

## Phase 51 - Production branch promotion

Required evidence:

- Pull request number.
- GitHub repository exactly matches `omparekh54-lgtm/india-ai-financial-analyst`.
- Source branch and target branch are recorded.
- Target branch is `main` or `master`.
- Expected promoted head SHA is recorded.
- Required checks are green.
- Review or owner approval is recorded.
- Protected environment approval is recorded.
- Merge strategy is recorded.
- Phase 47-50 deployment cutover gate is ready.
- No uncommitted deployment configuration drift exists.

This phase makes the GitHub branch promotion explicit before any production alias is moved.

## Phase 52 - Vercel production domain

Required evidence:

- Production deployment ID starts with `dpl_`.
- Production deployment is ready.
- Production commit SHA matches the promoted commit SHA.
- Production URL is `https://india-ai-financial-analyst.vercel.app`.
- Production URL is verified over HTTPS.
- Production alias is ready.
- Vercel root directory is `apps/web`.
- Framework is Next.js.
- Production environment file/config was detected.
- `NEXT_PUBLIC_API_BASE_URL` points to `https://api-production-d331d.up.railway.app`.
- Vercel runtime error count is zero.

This phase verifies that production is serving the exact artifact intended for release, with the frontend still wired to the live Railway API.

## Phase 53 - Backend post-promotion sync

Required evidence:

- Railway project ID and environment ID.
- Railway API deployment status is `SUCCESS`.
- Required worker services are `SUCCESS`.
- Railway branch strategy is recorded.
- Railway branch either matches the promoted branch or the temporary feature-branch/manual-pin strategy is explicitly accepted.
- `GET /health` returns HTTP 200.
- `GET /ready` returns HTTP 200.
- `/ready` reports `database_healthy: true`.
- CORS includes the production Vercel URL.
- Backend runtime blocker count is zero.
- Free-only mode remains enabled.
- Commercial launch remains disabled.

This phase keeps the backend aligned with the promoted frontend without accidentally enabling commercial launch before the real corpus and security gates are resolved.

## Phase 54 - Launch decision register

Required evidence:

- Launch decision is `go`, `conditional_go` or `blocked`.
- Known blockers are recorded.
- Real corpus gate status is recorded.
- Supabase security exception status is recorded.
- Sentry monitoring status is recorded.
- npm audit status is recorded.
- Rollback reference is recorded.
- Monitoring reference is recorded.
- Operator signoff reference is recorded.
- Conditional approval reference is recorded when the decision is `conditional_go`.

A `go` decision requires the real corpus gate to be passed, Supabase security status resolved, Sentry configured and npm audit clean. A `conditional_go` can pass only when the remaining accepted exceptions are explicitly referenced. A `blocked` decision can pass as an honest launch register only when blockers and handoff references are recorded.
