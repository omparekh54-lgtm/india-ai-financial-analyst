# Phases 31-36 - Post-Launch Acceptance

This block continues after the Phase 30 release gate. It is not a replacement for Phase 30 and must not be used to bypass missing deployment, auth, load, commercial approval, or real-company evidence.

The post-launch gate is intentionally evidence-driven:

```bash
python scripts/run_post_launch_acceptance_gate.py --plan-only
python scripts/run_post_launch_acceptance_gate.py --evidence-json post-launch-evidence.json
```

The evidence JSON must be collected from production systems such as Vercel, Supabase, GitHub Actions, Sentry/PostHog or equivalent observability tools, billing dashboards, and approved operator records. It must not contain secrets or secret-like keys.

The protected GitHub workflow is `.github/workflows/post-launch-acceptance.yml`. It is manual only, requires exact confirmation text `RUN_POST_LAUNCH_GATE`, uses read-only repository permissions, runs under the protected production environment, uploads the structured result, and intentionally does not reference GitHub secrets. Operators paste only non-secret evidence JSON.

## Phase 31 - Production observability baseline

Required evidence:

- HTTPS production deployment URL.
- Vercel project ID for the deployed application.
- Error monitoring configured.
- Privacy filtering enabled for logs/events.
- At least one alert route.
- Zero critical runtime errors in the measured 24-hour window.

A local build, preview URL, or CI success does not satisfy this phase.

## Phase 32 - Data freshness and drift control

Required evidence:

- Corpus readiness is green.
- No stale market data rows in the measured production window.
- No failed ingestion runs in the past 24 hours.
- Official feed lag is 120 minutes or less.
- All nine required macro series are present.
- Both required benchmark codes have bars.

This phase keeps the live corpus from silently aging after launch.

## Phase 33 - Research quality and calibration

Required evidence:

- At least 25 evaluated real reports.
- At least four distinct real sectors represented.
- Validated claim coverage of at least 90%.
- Unsupported claim rate of 2% or lower.
- Validator completion confirmed.
- No open calibration errors.

This phase protects the evidence-first promise after real users begin generating reports.

## Phase 34 - Security and advisor acceptance

Required evidence:

- Zero Supabase security warning lints.
- Zero RLS-enabled tables without policies.
- Zero critical dependency vulnerabilities.
- Zero exposed secret findings.
- Auth isolation passed against production.
- Explicit security review approval.

INFO-only unused-index notices do not block by themselves, but WARN/HIGH/CRITICAL security findings do. Backend-owned public tables should have explicit deny-all policies for `anon` and `authenticated` when direct client access is not part of the product surface.

## Phase 35 - Cost, quota and provider control

Required evidence:

- `FREE_ONLY=true` launch policy remains enabled unless the operator has explicitly approved a paid launch.
- Paid fallback routes are disabled.
- Monthly budget is configured.
- Usage caps are configured.
- Provider quota alerts are configured.
- Unapproved paid spend is zero.

Credential presence is not proof of quota, budget, or commercial readiness.

## Phase 36 - Rollback and incident readiness

Required evidence:

- Production backup has been verified.
- Rollback target deployment ID is recorded.
- Restore drill passed.
- Incident runbook URL is HTTPS and accessible to operators.
- On-call route is configured.
- Release owner approval is recorded.

A release can be technically deployed and still fail this phase if recovery has not been proven.

## Current status policy

Phase 31-36 can only pass after Phase 30 is truly green. If Phase 30 is blocked, the post-launch gate should remain planned or failed, not passed.

No synthetic/mock/sample/dummy/placeholder production evidence should be created to make this block pass.
