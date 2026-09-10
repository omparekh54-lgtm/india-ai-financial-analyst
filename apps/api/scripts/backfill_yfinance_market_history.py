from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import sentry_sdk
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.yahoo_finance import YahooFinanceDataError, YahooFinanceHistoryClient
from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.market import MarketBarIngestor
from app.ingestion.reference_provenance import (
    resolve_security,
    upsert_restricted_external_source,
)
from app.observability import configure_sentry

_INDIA_TIMEZONE = ZoneInfo("Asia/Kolkata")

# Railway drops log messages above ~500 lines/sec per replica. A run over the full NSE EQ
# universe emits one line per symbol, which exceeded that and marked the job CRASHED.
_PROGRESS_EVERY = 250


def _classify_failures(failed: list[dict[str, object]]) -> dict[str, int]:
    """Group failures by cause so operators can act instead of reading 2,300 log lines."""
    counts: dict[str, int] = {}
    for item in failed:
        message = str(item.get("error", "")).lower()
        if "delist" in message or "no data found" in message or "not found" in message:
            reason = "delisted_or_unknown_symbol"
        elif "rate" in message and "limit" in message:
            reason = "provider_rate_limited"
        elif "timeout" in message or "timed out" in message:
            reason = "provider_timeout"
        elif "empty" in message or "no price data" in message:
            reason = "no_bars_returned"
        else:
            reason = "other"
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


async def _record_ingestion_run(engine: AsyncEngine, summary: dict[str, object]) -> None:
    """Persist the run summary so freshness monitoring can see this pipeline.

    The Yahoo refresh previously wrote no ingestion_runs row at all, so the corpus looked
    as though nothing had been ingested since the last official-feed run even while market
    bars were being updated daily.
    """
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    insert into ingestion_runs
                      (pipeline, scope, status, started_at, completed_at, stats)
                    values
                      ('yfinance_market_history', 'nse_eq', :status, now(), now(), :stats)
                    """
                ),
                {"status": str(summary.get("status")), "stats": json.dumps(summary)},
            )
    except Exception as exc:  # noqa: BLE001 - reporting must never fail the ingestion run
        sentry_sdk.capture_exception(exc)
        print(json.dumps({"event": "ingestion_run_record_failed", "error": str(exc)}), flush=True)
_NSE_FALLBACK_START = date(1990, 1, 1)


@dataclass(frozen=True)
class HistoryTarget:
    security_id: UUID
    symbol: str
    legal_name: str
    exchange: str
    listing_date: date | None
    checkpoint: dict[str, object] | None = None


def previous_completed_day(*, now: datetime | None = None) -> date:
    """Return the last calendar day that cannot contain an in-progress India session."""
    current = now or datetime.now(_INDIA_TIMEZONE)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(_INDIA_TIMEZONE).date() - timedelta(days=1)


def refresh_end_day(*, now: datetime | None = None) -> date:
    current = now or datetime.now(_INDIA_TIMEZONE)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = current.astimezone(_INDIA_TIMEZONE)
    if local.weekday() < 5 and local.hour >= 18:
        return local.date()
    return local.date() - timedelta(days=1)


def refresh_start_day(
    target: HistoryTarget, *, end_day: date | None = None, recent_days: int | None = None
) -> date:
    checkpoint = target.checkpoint or {}
    if recent_days is not None:
        if end_day is None or not 30 <= recent_days <= 730:
            raise ValueError("recent history requires an end date and 30–730 calendar days")
        floor = max(
            target.listing_date or _NSE_FALLBACK_START, end_day - timedelta(days=recent_days)
        )
        latest_recent = checkpoint.get("recent_last_bar_date") or checkpoint.get("last_bar_date")
        if latest_recent:
            return max(floor, date.fromisoformat(str(latest_recent)) - timedelta(days=7))
        return floor
    latest = checkpoint.get("last_bar_date")
    if checkpoint.get("initial_history_imported") and latest:
        # Re-fetch an overlap to pick up recent provider corrections and holidays.
        return max(
            target.listing_date or _NSE_FALLBACK_START,
            date.fromisoformat(str(latest)) - timedelta(days=7),
        )
    return target.listing_date or _NSE_FALLBACK_START


async def save_checkpoint(
    engine: AsyncEngine, target: HistoryTarget, updates: dict[str, object]
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("""
                update securities set metadata = jsonb_set(
                    coalesce(metadata, '{}'::jsonb), '{yahoo_history_import}',
                    coalesce(metadata->'yahoo_history_import', '{}'::jsonb)
                        || cast(:updates as jsonb)), updated_at = now()
                where id = :security_id
            """),
            {"security_id": target.security_id, "updates": json.dumps(updates)},
        )


def resolve_date_range(
    *,
    from_date: date | None,
    to_date: date | None,
    lookback_days: int | None,
    today: date | None = None,
) -> tuple[date, date]:
    end_date = to_date or today or previous_completed_day()
    if lookback_days is not None:
        if lookback_days < 1 or lookback_days > 365:
            raise ValueError("--lookback-days must be between 1 and 365")
        start_date = end_date - timedelta(days=lookback_days)
    elif from_date is not None:
        start_date = from_date
    else:
        raise ValueError("one of --from-date or --lookback-days is required")
    if start_date > end_date:
        raise ValueError("--from-date cannot be after --to-date")
    return start_date, end_date


async def _target_for_identifier(engine: AsyncEngine, identifier: str) -> HistoryTarget:
    security_id, legal_name = await resolve_security(engine, identifier)
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """
                    select nse_symbol, bse_code, primary_exchange,
                           metadata->>'date_of_listing' as date_of_listing
                    from securities where id=:security_id
                    """
                    ),
                    {"security_id": security_id},
                )
            )
            .mappings()
            .one()
        )
    exchange = str(row["primary_exchange"] or "").upper()
    symbol = str(row["nse_symbol"] if exchange == "NSE" else row["bse_code"] or "")
    if exchange not in {"NSE", "BSE"} or not symbol:
        raise ValueError(f"security has no Yahoo-compatible NSE/BSE symbol: {identifier}")
    return HistoryTarget(
        security_id,
        symbol.upper(),
        legal_name,
        exchange,
        _parse_listing_date(row.get("date_of_listing")),
    )


async def _all_targets(
    engine: AsyncEngine,
    *,
    limit: int,
    after_symbol: str | None,
    daily_refresh: bool = False,
    to_date: date | None = None,
    supported_only: bool = False,
) -> list[HistoryTarget]:
    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        """
                    select s.id, s.nse_symbol, s.legal_name,
                           s.metadata->>'date_of_listing' as date_of_listing,
                           s.metadata->'yahoo_history_import' as checkpoint
                    from securities s
                    left join lateral (
                        select max(mb.ts) as latest_bar_ts
                        from market_bars mb
                        where mb.security_id = s.id
                          and mb.interval = '1d'
                          and mb.provider = 'yfinance'
                          and mb.source_id is not null
                    ) coverage on true
                    where s.primary_exchange='NSE'
                      and coalesce(s.metadata->>'nse_series', 'EQ')='EQ'
                      and s.nse_symbol is not null
                      and (not :supported_only or coverage.latest_bar_ts is not null)
                      and (cast(:after_symbol as text) is null
                           or s.nse_symbol > cast(:after_symbol as text))
                      and (not :daily_refresh or (
                          coalesce(s.metadata->'yahoo_history_import'->>'checked_through', '')
                              < cast(:to_date as text)
                          and coalesce(
                              s.metadata->'yahoo_history_import'->>'retry_after', '')
                              < cast(:now as text)))
                    order by coverage.latest_bar_ts asc nulls first, s.nse_symbol
                    limit :limit
                    """
                    ),
                    {
                        "after_symbol": after_symbol.upper() if after_symbol else None,
                        "limit": limit,
                        "daily_refresh": daily_refresh,
                        "supported_only": supported_only,
                        "to_date": to_date.isoformat() if to_date else "",
                        "now": datetime.now(UTC).isoformat(),
                    },
                )
            )
            .mappings()
            .all()
        )
    return [
        HistoryTarget(
            UUID(str(row["id"])),
            str(row["nse_symbol"]),
            str(row["legal_name"]),
            "NSE",
            _parse_listing_date(row.get("date_of_listing")),
            row.get("checkpoint"),
        )
        for row in rows
    ]


async def _run() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import real Yahoo Finance market history for internal research. "
            "Data is delayed/restricted and is never labeled exchange-certified or "
            "commercially approved."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--security", action="append", help="NSE symbol, BSE code, or ISIN")
    group.add_argument("--all", action="store_true", help="Process a bounded NSE batch")
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument("--from-date", type=date.fromisoformat)
    date_group.add_argument(
        "--lookback-days",
        type=int,
        help="Rolling calendar-day window ending at --to-date or the previous India calendar day",
    )
    date_group.add_argument(
        "--from-listing",
        action="store_true",
        help=(
            "Fetch each security from its official listing date. If listing metadata is "
            "unavailable, use the conservative NSE fallback start."
        ),
    )
    parser.add_argument("--to-date", type=date.fromisoformat)
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--after-symbol")
    parser.add_argument("--request-delay-seconds", type=float, default=0.5)
    parser.add_argument("--confirm-yahoo-research-use", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--supported-only", action="store_true",
        help="Refresh only securities that already have stored Yahoo daily bars",
    )
    parser.add_argument(
        "--recent-history-days",
        type=int,
        help="Daily refresh only: bound downloads to 30–730 calendar days; "
        "does not delete existing bars or mark full history complete",
    )
    parser.add_argument(
        "--daily-refresh",
        action="store_true",
        help="Sweep all eligible stocks with durable checkpoints and incremental daily updates",
    )
    args = parser.parse_args()

    if args.supported_only and (not args.daily_refresh or args.recent_history_days is None):
        parser.error("--supported-only requires --daily-refresh and --recent-history-days")
    if args.recent_history_days is not None and (
        not args.daily_refresh or not 30 <= args.recent_history_days <= 730
    ):
        parser.error("--recent-history-days requires --daily-refresh and a value from 30 to 730")

    if args.daily_refresh and (not args.all or not args.from_listing or args.interval != "1d"):
        parser.error("--daily-refresh requires --all --from-listing --interval 1d")
    to_date = args.to_date or (
        refresh_end_day() if args.daily_refresh else previous_completed_day()
    )
    from_date: date | None = None
    if not args.from_listing:
        try:
            from_date, to_date = resolve_date_range(
                from_date=args.from_date,
                to_date=to_date,
                lookback_days=args.lookback_days,
            )
        except ValueError as exc:
            parser.error(str(exc))
    max_limit = 10000 if args.daily_refresh else 100
    if args.limit < 1 or args.limit > max_limit:
        parser.error(f"--limit must be between 1 and {max_limit}")
    if args.request_delay_seconds < 0 or args.request_delay_seconds > 10:
        parser.error("--request-delay-seconds must be between 0 and 10")
    if args.after_symbol and not args.all:
        parser.error("--after-symbol can only be used with --all")
    if not args.dry_run and not args.confirm_yahoo_research_use:
        parser.error("--confirm-yahoo-research-use is required for writes")

    settings = get_settings()
    if not settings.database_url:
        parser.error("DATABASE_URL must be configured")
    configure_sentry(settings, service="yfinance-eod-importer")
    engine = create_database_engine(settings.database_url)
    try:
        if not args.dry_run:
            async with engine.connect() as connection:
                readonly = await connection.scalar(text("show transaction_read_only"))
                database_bytes = await connection.scalar(
                    text("select pg_database_size(current_database())")
                )
            if readonly == "on":
                print(json.dumps({"status": "blocked", "reason": "database_read_only"}), flush=True)
                sentry_sdk.capture_message(
                    "Market import blocked: database read-only", level="error"
                )
                sentry_sdk.flush(timeout=2)
                return 2
            if int(database_bytes or 0) >= 450_000_000:
                print(json.dumps({"status": "blocked", "reason": "database_capacity"}), flush=True)
                sentry_sdk.capture_message("Market import blocked: storage headroom low", level="error")
                sentry_sdk.flush(timeout=2)
                return 2
        targets = (
            await _all_targets(
                engine,
                limit=args.limit,
                after_symbol=args.after_symbol,
                daily_refresh=args.daily_refresh,
                to_date=to_date,
                supported_only=args.supported_only,
            )
            if args.all
            else [await _target_for_identifier(engine, value) for value in args.security or []]
        )
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "status": "dry_run",
                        "provider": "yfinance",
                        "allowed_use": "internal_research",
                        "commercial_display_approved": False,
                        "target_count": len(targets),
                        "to_date": to_date.isoformat(),
                        "targets": [
                            {
                                **asdict(target),
                                "planned_from_date": (
                                    refresh_start_day(
                                        target,
                                        end_day=to_date,
                                        recent_days=args.recent_history_days,
                                    )
                                    if args.daily_refresh
                                    else target.listing_date or _NSE_FALLBACK_START
                                    if args.from_listing
                                    else from_date
                                ),
                            }
                            for target in targets
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            )
            return 0

        client = YahooFinanceHistoryClient()
        ingestor = MarketBarIngestor(engine)
        results: list[dict[str, object]] = []
        failures = 0
        for position, target in enumerate(targets):
            if position and position % 100 == 0:
                async with engine.connect() as connection:
                    database_bytes = await connection.scalar(
                        text("select pg_database_size(current_database())")
                    )
                if int(database_bytes or 0) >= 450_000_000:
                    sentry_sdk.capture_message("Market import stopped: storage headroom low", level="error")
                    sentry_sdk.flush(timeout=2)
                    print(json.dumps({"status": "blocked", "reason": "database_capacity"}), flush=True)
                    return 2
            try:
                target_from_date = (
                    target.listing_date or _NSE_FALLBACK_START if args.from_listing else from_date
                )
                if args.daily_refresh:
                    target_from_date = refresh_start_day(
                        target, end_day=to_date, recent_days=args.recent_history_days
                    )
                if target_from_date is None:
                    raise ValueError("history start date could not be resolved")
                if target_from_date > to_date:
                    raise ValueError(
                        f"history start {target_from_date.isoformat()} is after "
                        f"end {to_date.isoformat()}"
                    )
                fetched = await client.fetch_history(
                    target.symbol,
                    exchange=target.exchange,
                    from_date=target_from_date,
                    to_date=to_date,
                    interval=args.interval,
                )
                source_id = await upsert_restricted_external_source(
                    engine,
                    security_id=target.security_id,
                    source_type="restricted_market_data",
                    source_uri=(
                        f"{fetched.source_url}?from={target_from_date}&to={to_date}"
                        f"&interval={args.interval}"
                    ),
                    title=f"Yahoo Finance delayed market history - {target.symbol}",
                    published_at=max(bar.ts for bar in fetched.bars),
                    checksum=fetched.response_sha256,
                    freshness="historical" if args.interval == "1d" else "near_live",
                    metadata={
                        "provider": "yfinance",
                        "yahoo_symbol": fetched.yahoo_symbol,
                        "interval": args.interval,
                        "from_date": target_from_date.isoformat(),
                        "to_date": to_date.isoformat(),
                        "range_policy": (
                            "recent_daily_window"
                            if args.recent_history_days is not None
                            else "incremental_daily"
                            if args.daily_refresh
                            and (target.checkpoint or {}).get("initial_history_imported")
                            else "listing_to_previous_day"
                            if args.from_listing
                            else "bounded"
                        ),
                        "listing_date_available": target.listing_date is not None,
                        "importer": "backfill_yfinance_market_history",
                    },
                )
                ingested = await ingestor.ingest_security_bars(
                    security_id=target.security_id,
                    bars=list(fetched.bars),
                    source_id=source_id,
                )
                if args.daily_refresh:
                    first = min(bar.ts for bar in fetched.bars).astimezone(_INDIA_TIMEZONE).date()
                    last = max(bar.ts for bar in fetched.bars).astimezone(_INDIA_TIMEZONE).date()
                    await save_checkpoint(
                        engine,
                        target,
                        {
                            "initial_history_imported": (
                                bool((target.checkpoint or {}).get("initial_history_imported"))
                                if args.recent_history_days is not None
                                else True
                            ),
                            "status": (
                                "imported_recent_history"
                                if args.recent_history_days is not None
                                else "imported_available_history"
                            ),
                            "checked_through": to_date.isoformat(),
                            "first_bar_date": (target.checkpoint or {}).get(
                                "first_bar_date", first.isoformat()
                            ),
                            "last_bar_date": last.isoformat(),
                            "recent_last_bar_date": last.isoformat(),
                            "listing_date": target.listing_date.isoformat()
                            if target.listing_date
                            else None,
                            "last_source_id": str(source_id),
                            "last_attempt_at": datetime.now(UTC).isoformat(),
                            "retry_after": "",
                            "last_error": None,
                        },
                    )
                results.append(
                    {
                        "symbol": target.symbol,
                        "ok": True,
                        "bar_count": ingested["normalized_count"],
                        "source_id": str(source_id),
                    }
                )
            except (YahooFinanceDataError, ValueError) as exc:
                failures += 1
                results.append({"symbol": target.symbol, "ok": False, "error": str(exc)})
                sentry_sdk.capture_exception(exc)
                if args.daily_refresh:
                    await save_checkpoint(
                        engine,
                        target,
                        {
                            "status": "failed_or_unavailable",
                            "last_error": str(exc)[:500],
                            "last_attempt_at": datetime.now(UTC).isoformat(),
                            "retry_after": (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                        },
                    )
            # One log line per symbol overwhelmed Railway's 500 lines/sec replica limit
            # (12,454 messages dropped, job marked CRASHED). Emit a periodic heartbeat
            # instead; per-symbol outcomes are persisted to ingestion_runs and checkpoints.
            processed = position + 1
            if processed % _PROGRESS_EVERY == 0 or processed == len(targets):
                print(
                    json.dumps(
                        {
                            "event": "progress",
                            "processed": processed,
                            "total": len(targets),
                            "failures": failures,
                        }
                    ),
                    flush=True,
                )
            if position + 1 < len(targets) and args.request_delay_seconds:
                await asyncio.sleep(args.request_delay_seconds)

        failed = [item for item in results if not item.get("ok")]
        summary = {
            "status": "completed" if failures == 0 else "completed_with_failures",
            "provider": "yfinance",
            "allowed_use": "internal_research",
            "commercial_display_approved": False,
            "target_count": len(targets),
            "success_count": len(targets) - failures,
            "failure_count": failures,
            "failure_reasons": _classify_failures(failed),
            # Bounded sample only. Dumping every result re-created the log storm this
            # script was crashing on.
            "failed_symbols_sample": [str(item.get("symbol")) for item in failed[:50]],
            "failed_symbols_truncated": max(0, len(failed) - 50),
            "next_after_symbol": targets[-1].symbol if args.all and targets else None,
        }
        print(json.dumps(summary, sort_keys=True), flush=True)
        await _record_ingestion_run(engine, summary)

        # Partial symbol failures are an expected outcome (delistings, suspensions, provider
        # gaps) and must not present as a crashed job. PROJECT_INTENT.md: "Scheduled imports
        # may finish as partial success when individual securities fail, but failures must be
        # summarized, retried, and alerted rather than hidden". A non-zero exit here is what
        # made Railway report CRASHED for an otherwise successful run.
        if failures and failures == len(targets):
            return 1  # nothing at all succeeded: a real failure
        return 0
    finally:
        await engine.dispose()


def _parse_listing_date(value: object) -> date | None:
    if value is None:
        return None
    cleaned = str(value).strip().upper()
    if not cleaned or cleaned in {"-", "--", "NA", "N/A"}:
        return None
    for pattern in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(cleaned, pattern).replace(tzinfo=_INDIA_TIMEZONE).date()
        except ValueError:
            continue
    return None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
