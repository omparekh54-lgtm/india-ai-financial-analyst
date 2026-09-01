# Phases 43-46 - Production Activation Readiness

This block turns the Phase 37-42 deployment-readiness checklist into a stricter pre-deploy activation gate. It does not claim the app is live. It verifies that the production target, environment, real Supabase data and final security posture are ready before deployment proceeds.

Run locally or in CI with non-secret evidence:

```bash
python scripts/run_production_activation_gate.py --plan-only
python scripts/run_production_activation_gate.py --evidence-json production-activation-evidence.json
```

The evidence JSON must contain only booleans, IDs, URLs, counts and approval references. It must not contain access tokens, provider keys, passwords, client secrets or service-role credentials.

## Phase 43 - Vercel live project

Required evidence:

- Vercel team ID.
- Vercel project ID.
- Project name.
- GitHub repository link exactly matching `omparekh54-lgtm/india-ai-financial-analyst`.
- Production branch.
- Root directory.
- Framework.
- HTTPS preview deployment URL.
- Preview deployment ready.
- Production domains HTTPS-ready.

Current status: red. The connected Vercel team has projects, but none is linked to this repository yet.

## Phase 44 - Production environment

Required evidence:

- Vercel environment pulled successfully.
- Vercel org/project IDs configured.
- Database URL configured.
- Supabase URL configured.
- Supabase publishable key configured.
- API base URL configured.
- Web app URL configured.
- CORS origins are HTTPS-only.
- Frontend uses publishable Supabase key only.
- Service role is absent from frontend/runtime-public vars.
- No plaintext credentials exist in the repository.

This phase verifies configuration presence and safety. Secret values must remain in the deployment platform or protected CI environment.

## Phase 45 - Supabase real data readiness

Required evidence:

- At least 1,000 real securities.
- Per-security market bars present.
- Evidence chunks present.
- Research jobs present.
- Research reports present.
- At least five representative real-company research jobs.
- Agent 15 completed for those representative jobs.
- Agent 16 completed for those representative jobs.
- Zero non-production source rows.
- Production corpus gate ready.

Current live snapshot: 2,302 securities exist, but market bars, evidence chunks and research jobs are still zero. This phase must stay red until the real corpus pipeline and representative research acceptance have completed.

## Phase 46 - Supabase security finalization

Required evidence:

- Supabase project status is `ACTIVE_HEALTHY`.
- Database/Postgres version recorded.
- Migrations applied through `0026_backend_only_rls_deny_policies.sql`.
- Latest required migration matches `0026_backend_only_rls_deny_policies.sql`.
- RLS no-policy advisor count is zero.
- Security error count is zero.
- Security warnings are zero, or managed-platform warnings have an accepted exception.
- `pg_net` public-schema exception is accepted or not applicable.
- Performance blocker count is zero.

Current live status: project is healthy, the remaining security warning is `pg_net` in `public`, and performance advisories are INFO-level unused indexes on a seed/empty workload. The `pg_net` warning still needs formal managed-platform exception/approval before this phase can be marked green.
