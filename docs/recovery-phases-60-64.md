# Recovery acceptance status

This is an implementation checkpoint, not production acceptance.

## Phase 60: storage recovery — blocked

The database was observed at 1,580 MB with default_transaction_read_only=on.
No historical rows have been deleted. A verified, restorable full backup is
required before retention cleanup. The earlier single-stock compression result
does not prove full backup completeness or total archive size. Do not reset the
database read-only setting merely to resume imports above its quota.

## Phase 61: bounded daily updates — partial code implementation

The importer accepts `--recent-history-days 400` together with its existing
`--all --from-listing --daily-refresh --interval 1d` arguments. This is a calendar
day window, not a guaranteed trading-session count. It bounds the initial recent
fetch and resumes with a seven-day overlap. It does not delete existing data.
Recent-only results retain a false full-history checkpoint unless full history
was already imported. Sources identify the range as `recent_daily_window`.
Dry runs show the actual planned range. Writes stop early when the database is
read-only. This mode alone does not enforce disk retention or reclaim space.

Do not activate this in production until backup verification, retention cleanup,
disk headroom checks and query-label review pass. In particular, lifetime highs
and drawdowns must not be labelled as lifetime statistics from a recent window.
The existing weekday 12:30 UTC / 18:00 IST schedule is unchanged.

Pending: authoritative holiday/session coverage, bounded provider retries,
capacity checks during a sweep, stale-data alerts and an observed scheduled run.

## Phase 62: fresh research — pending production validation

Required checks: permitted provider access, dated news/fundamentals, source
provenance, stock-specific freshness handling, report persistence, honest
missing-data presentation. Market-data freshness alone is insufficient.

## Phase 63: worker recovery — partial code implementation

Research queue startup and polling retry SQLAlchemy failures with delays of
5, 10, 20 and 40 seconds, stopping after five consecutive failures. Recovery
requeues stale jobs before polling again. Error reporting uses a fixed message
instead of database exception text. Cancellation is not swallowed.

Live Sentry delivery and worker restart remain unverified. No Sentry connection
was available among the tools exposed during this implementation.

## Phase 64: production acceptance — pending

Require authenticated desktop/mobile browser checks, successful report creation
and retrieval, correctly dated charts and sources, worker recovery, private
error reporting, and a real scheduled update. Neither a passing unit test nor
a successful deployment status closes these acceptance checks.
