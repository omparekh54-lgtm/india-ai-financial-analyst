# Phases 37-42 - Deployment Readiness

This block follows the Phase 31-36 post-launch acceptance implementation. It prepares the project for a real production deployment without pretending that deployment has already happened.

Run locally or inside CI with non-secret evidence:

```bash
python scripts/run_deployment_readiness_gate.py --plan-only
python scripts/run_deployment_readiness_gate.py --evidence-json deployment-readiness-evidence.json
```

The evidence JSON must contain only booleans, IDs, URLs, counts and approval references. It must not contain access tokens, provider keys, passwords, client secrets or service-role credentials.

## Phase 37 - Vercel project linkage

Required evidence:

- Vercel team ID.
- Vercel project ID.
- Project name.
- GitHub repository link.
- Production branch.
- Root directory.
- Framework.
- Preview deployments enabled.

This phase is red until a real Vercel project exists for this repository.

## Phase 38 - Environment contract

Required evidence:

- Vercel token configured as a protected secret.
- Vercel org ID configured.
- Vercel project ID configured.
- Database URL configured.
- Supabase URL configured.
- Supabase publishable key configured.
- API base URL configured.
- Web app URL configured.
- CORS origins are HTTPS-only.
- No plaintext credentials are present in the repository.

The gate checks evidence that secrets are configured; it must never receive the secret values.

## Phase 39 - Database migration readiness

Required evidence:

- Supabase project healthy.
- Migrations applied through `0026_backend_only_rls_deny_policies.sql`.
- Latest required migration matches `0026_backend_only_rls_deny_policies.sql`.
- RLS no-policy advisor count is zero.
- Security warnings are zero, or managed-platform warnings have a documented exception.

The current managed warning is expected to remain red unless Supabase confirms an exception for non-relocatable `pg_net` in `public`.

## Phase 40 - Build artifact readiness

Required evidence:

- GitHub CI success.
- API Ruff passed.
- API typecheck passed.
- API tests passed.
- Web production build passed.
- Production Compose config passed.
- API Dockerfile validation passed.
- Deployment commit SHA recorded.

This phase ensures the exact commit intended for deployment has passed the build surface.

## Phase 41 - Auth and traffic readiness

Required evidence:

- Supabase Auth Site URL configured.
- Redirect URLs configured.
- Owner test user ready.
- Other-user isolation test account ready.
- Smoke-test user ready.
- Rate limits configured.
- Deployment smoke plan ready.

Tokens are still generated later as short-lived environment-only values for Phase 30; they do not belong in this evidence file.

## Phase 42 - Final deployment runbook

Required evidence:

- Phase 30 gate plan ready.
- Phase 31-36 post-launch gate plan ready.
- Rollback plan ready.
- DNS cutover plan ready.
- Commercial approval plan ready.
- Launch owner named.

This phase is the final deployment checklist before the production release workflow is run.
