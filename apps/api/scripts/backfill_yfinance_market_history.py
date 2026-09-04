from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from uuid import UUID

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


@dataclass(frozen=True)
class HistoryTarget:
    security_id: UUID
    symbol: str
    legal_name: str
    exchange: str


async def _target_for_identifier(engine: AsyncEngine, identifier: str) -> HistoryTarget:
    security_id, legal_name = await resolve_security(engine, identifier)
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        """
                    select nse_symbol, bse_code, primary_exchange
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
    return HistoryTarget(security_id, symbol.upper(), legal_name, exchange)


async def _all_targets(
    engine: AsyncEngine,
    *,
    limit: int,
    after_symbol: str | None,
) -> list[HistoryTarget]:
    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        """
                    select id, nse_symbol, legal_name
                    from securities
                    where primary_exchange='NSE'
                      and coalesce(metadata->>'nse_series', 'EQ')='EQ'
                      and nse_symbol is not null
                      and (:after_symbol is null or nse_symbol > :after_symbol)
                    order by nse_symbol
                    limit :limit
                    """
                    ),
                    {
                        "after_symbol": after_symbol.upper() if after_symbol else None,
                        "limit": limit,
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
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument("--to-date", type=date.fromisoformat, default=datetime.now(UTC).date())
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--after-symbol")
    parser.add_argument("--request-delay-seconds", type=float, default=0.5)
    parser.add_argument("--confirm-yahoo-research-use", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.from_date > args.to_date:
        parser.error("--from-date cannot be after --to-date")
    if args.limit < 1 or args.limit > 100:
        parser.error("--limit must be between 1 and 100")
    if args.request_delay_seconds < 0 or args.request_delay_seconds > 10:
        parser.error("--request-delay-seconds must be between 0 and 10")
    if args.after_symbol and not args.all:
        parser.error("--after-symbol can only be used with --all")
    if not args.dry_run and not args.confirm_yahoo_research_use:
        parser.error("--confirm-yahoo-research-use is required for writes")

    settings = get_settings()
    if not settings.database_url:
        parser.error("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        targets = (
            await _all_targets(engine, limit=args.limit, after_symbol=args.after_symbol)
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
                        "targets": [asdict(target) for target in targets],
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
            try:
                fetched = await client.fetch_history(
                    target.symbol,
                    exchange=target.exchange,
                    from_date=args.from_date,
                    to_date=args.to_date,
                    interval=args.interval,
                )
                source_id = await upsert_restricted_external_source(
                    engine,
                    security_id=target.security_id,
                    source_type="restricted_market_data",
                    source_uri=fetched.source_url,
                    title=f"Yahoo Finance delayed market history - {target.symbol}",
                    published_at=max(bar.ts for bar in fetched.bars),
                    checksum=fetched.response_sha256,
                    freshness="historical" if args.interval == "1d" else "near_live",
                    metadata={
                        "provider": "yfinance",
                        "yahoo_symbol": fetched.yahoo_symbol,
                        "interval": args.interval,
                        "from_date": args.from_date.isoformat(),
                        "to_date": args.to_date.isoformat(),
                        "importer": "backfill_yfinance_market_history",
                    },
                )
                ingested = await ingestor.ingest_security_bars(
                    security_id=target.security_id,
                    bars=list(fetched.bars),
                    source_id=source_id,
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
            if position + 1 < len(targets) and args.request_delay_seconds:
                await asyncio.sleep(args.request_delay_seconds)

        print(
            json.dumps(
                {
                    "status": "completed" if failures == 0 else "completed_with_failures",
                    "provider": "yfinance",
                    "allowed_use": "internal_research",
                    "commercial_display_approved": False,
                    "target_count": len(targets),
                    "failure_count": failures,
                    "next_after_symbol": targets[-1].symbol if args.all and targets else None,
                    "results": results,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if failures == 0 else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
