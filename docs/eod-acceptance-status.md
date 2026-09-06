# End-of-day research acceptance checkpoint — 2026-09-06

## Target

No Upstox credentials or live/intraday feed is required for the revised product.
Import available daily OHLCV from listing metadata through the previous India calendar day.
Weekends and holidays contain no candles; never invent missing sessions or assume Yahoo
history begins at the IPO. Exchange listing dates can differ from the original IPO date.

## Implementation

- Importer supports `--from-listing`; default end date excludes today's India session.
- Research selects a single sourced provider (largest eligible history, deterministic tie-break),
  excluding today's session, and supplies recent bars plus full-history aggregates.
- Raw price-return and drawdown statistics explicitly warn about corporate actions and gaps.
- UI replaces the unavailable Upstox connect action with end-of-day status.
- Authenticated browser failures use a bounded, per-process reporting rate limit.
- Browser text, stack and metadata are discarded before logging/reporting to protect secrets.
- Reporting works only after sign-in. Server errors use the existing Sentry SDK.

## Not yet accepted

- Changes have not been deployed and listing-to-date backfill has not been executed.
- SENTRY_DSN is absent from the last inspected Railway environment. No Sentry delivery or alerts
  have been verified. Configure it for API, research/official workers and the importer.
- Groq/Gemini/NVIDIA/Tavily/FRED credentials were absent from the inspected production variables.
  Install valid rotated keys server-side; do not commit or paste them into reports.
- Full browser acceptance is blocked: agent-browser daemon failed to start twice in this workspace.
- Authenticated sign-in, research, report/export and watchlist workflows remain unverified.
- Full-universe history coverage, corporate-action adjustment, source-use approval and feed
  freshness remain separate data acceptance requirements, not proven by deployment health.

## Rollout sequence

1. Review and deploy this checkpoint after the browser acceptance environment is available.
2. Run a bounded five-security `--from-listing --interval 1d` import with research-use confirmation.
3. Verify first/last India session dates, bar counts, provenance and gaps in Supabase.
4. Paginate the remaining intended universe with `--all --limit 25 --after-symbol` and retain
   failed-symbol retries. Do not claim whole-universe coverage after the first batch.
5. Refresh after midnight IST each day (including Saturday to ingest Friday), overlapping recent
   days for corrections. Existing weekday evening cron has not yet been changed.
6. Verify a controlled Sentry event and configured alert destination, then run authenticated
   production browser acceptance. Do not record green acceptance before these checks pass.
