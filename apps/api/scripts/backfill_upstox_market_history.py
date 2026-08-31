from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.upstox_history import UpstoxHistoricalClient, UpstoxHistoricalDataError
from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.market import MarketBarIngestor
from app.ingestion.reference_provenance import (
    resolve_security,
    upsert_reference_source,
    validate_reference_approval,
)

DEFAULT_APPROVAL_REFERENCE = "SG-2026-08-31-01"
DEFAULT_TOKEN_ENV = "UPSTOX_DATA_ACCESS_TOKEN"


@dataclass(frozen=True)
class HistoryTarget:
    security_id: UUID
    symbol: str
    legal_name: str
    instrument_key: str


async def _target_for_identifier(engine: AsyncEngine, identifier: str) -> HistoryTarget:
    security_id, legal_name = await resolve_security(engine, identifier)
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    select s.nse_symbol, pi.instrument_id
                    from securities s
                    join provider_instruments pi
                      on pi.security_id = s.id and pi.provider = 'upstox'
                    where s.id = :security_id
                    order by pi.updated_at desc
                    limit 1
                    """
                ),
                {"security_id": security_id},
            )
        ).mappings().first()
    if row is None or not row.get("instrument_id"):
        raise ValueError(f"security has no Upstox instrument mapping: {identifier}")
    return HistoryTarget(
        security_id=security_id,
        symbol=str(row.get("nse_symbol") or identifier).upper(),
        legal_name=legal_name,
        instrument_key=str(row["instrument_id"]),
    )


async def _all_targets(
    engine: AsyncEngine,
    *,
    limit: int,
    after_symbol: str | None,
) -> list[HistoryTarget]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    select distinct on (s.id)
                           s.id, s.nse_symbol, s.legal_name, pi.instrument_id
                    from securities s
                    join provider_instruments pi
                      on pi.security_id = s.id and pi.provider = 'upstox'
                    where s.primary_exchange = 'NSE'
                      and coalesce(s.metadata->>'nse_series', 'EQ') = 'EQ'
                      and s.nse_symbol is not null
                      and (:after_symbol is null or s.nse_symbol > :after_symbol)
                    order by s.id, pi.updated_at desc
                    """
                ),
                {"after_symbol": after_symbol.upper() if after_symbol else None},
            )
        ).mappings().all()
    targets = [
        HistoryTarget(
            security_id=UUID(str(row["id"])),
            symbol=str(row["nse_symbol"]).upper(),
            legal_name=str(row["legal_name"]),
            instrument_key=str(row["instrument_id"]),
        )
        for row in rows
    ]
    return sorted(targets, key=lambda item: item.symbol)[:limit]


async def _run() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill real daily NSE cash-equity history from Upstox V3. "
            "Uses a separate operator token and never reads personal broker OAuth tokens."
        )
    )
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--security", action="append", help="NSE symbol/BSE code/ISIN")
    target_group.add_argument("--all", action="store_true", help="Process a bounded NSE batch")
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--to-date",
        type=date.fromisoformat,
        default=datetime.now(UTC).date(),
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--after-symbol")
    parser.add_argument("--access-token-env", default=DEFAULT_TOKEN_ENV)
    parser.add_argument("--approval-reference", default=DEFAULT_APPROVAL_REFERENCE)
    parser.add_argument("--request-delay-seconds", type=float, default=0.15)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.from_date > args.to_date:
        parser.error("--from-date cannot be after --to-date")
    if args.limit < 1 or args.limit > 500:
        parser.error("--limit must be between 1 and 500")
    if args.request_delay_seconds < 0 or args.request_delay_seconds > 10:
        parser.error("--request-delay-seconds must be between 0 and 10")
    if args.after_symbol and not args.all:
        parser.error("--after-symbol can only be used with --all")

    approval = validate_reference_approval(
        "https://api.upstox.com/v3/historical-candle",
        args.approval_reference,
    )
    if approval.provenance_class != "licensed_or_approved":
        parser.error("Upstox history must remain classified as licensed_or_approved")

    settings = get_settings()
    if not settings.database_url:
        parser.error("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        if args.all:
            targets = await _all_targets(
                engine,
                limit=args.limit,
                after_symbol=args.after_symbol,
            )
        else:
            targets = [
                await _target_for_identifier(engine, identifier)
                for identifier in args.security or []
            ]

        if not targets:
            print(json.dumps({"status": "completed", "target_count": 0, "results": []}))
            return 0

        token = os.environ.get(args.access_token_env, "").strip()
        if not args.dry_run and not token:
            parser.error(
                f"{args.access_token_env} must contain an operator Upstox access token"
            )
        client = UpstoxHistoricalClient(token or "dry-run-token")

        if args.dry_run:
            payload = {
                "status": "dry_run",
                "provenance_class": approval.provenance_class,
                "approval_reference": approval.approval_reference,
                "from_date": args.from_date.isoformat(),
                "to_date": args.to_date.isoformat(),
                "target_count": len(targets),
                "targets": [
                    {
                        "symbol": target.symbol,
                        "legal_name": target.legal_name,
                        "instrument_key": target.instrument_key,
                        "request_url": client.request_url(
                            target.instrument_key,
                            from_date=args.from_date,
                            to_date=args.to_date,
                        ),
                    }
                    for target in targets
                ],
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        ingestor = MarketBarIngestor(engine)
        results: list[dict[str, object]] = []
        failure_count = 0
        for position, target in enumerate(targets):
            try:
                fetched = await client.fetch_daily(
                    target.instrument_key,
                    from_date=args.from_date,
                    to_date=args.to_date,
                )
                if not fetched.bars:
                    results.append(
                        {
                            "symbol": target.symbol,
                            "ok": True,
                            "bar_count": 0,
                            "warning": "Upstox returned no candles for the requested window",
                        }
                    )
                else:
                    source_id = await upsert_reference_source(
                        engine,
                        security_id=target.security_id,
                        source_type="market_history",
                        source_uri=fetched.source_url,
                        title=f"Upstox V3 daily history - {target.symbol}",
                        published_at=None,
                        checksum=fetched.response_sha256,
                        metadata={
                            "provider": "upstox",
                            "instrument_key": target.instrument_key,
                            "interval": "1d",
                            "from_date": args.from_date.isoformat(),
                            "to_date": args.to_date.isoformat(),
                            "governance_record": DEFAULT_APPROVAL_REFERENCE,
                        },
                        approval_reference=args.approval_reference,
                    )
                    result = await ingestor.ingest_security_bars(
                        security_id=target.security_id,
                        bars=list(fetched.bars),
                        source_id=source_id,
                    )
                    results.append(
                        {
                            "symbol": target.symbol,
                            "ok": True,
                            "source_id": str(source_id),
                            "response_sha256": fetched.response_sha256,
                            "bar_count": result["normalized_count"],
                        }
                    )
            except (UpstoxHistoricalDataError, ValueError) as exc:
                failure_count += 1
                results.append(
                    {
                        "symbol": target.symbol,
                        "ok": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
            if position + 1 < len(targets) and args.request_delay_seconds:
                await asyncio.sleep(args.request_delay_seconds)

        output = {
            "status": "completed" if failure_count == 0 else "completed_with_failures",
            "data_policy": "real_provider_data_only",
            "provider": "upstox",
            "provenance_class": approval.provenance_class,
            "approval_reference": approval.approval_reference,
            "from_date": args.from_date.isoformat(),
            "to_date": args.to_date.isoformat(),
            "target_count": len(targets),
            "failure_count": failure_count,
            "next_after_symbol": targets[-1].symbol if args.all else None,
            "results": results,
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0 if failure_count == 0 else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
