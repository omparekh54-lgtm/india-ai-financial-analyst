from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import text

from app.core.config import get_settings
from app.db import create_database_engine

DEFAULT_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
_ALLOWED_REMOTE_HOSTS = {"nsearchives.nseindia.com", "www.nseindia.com", "nseindia.com"}


def normalize_alias(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def parse_rows(content: str, *, series: str = "EQ") -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    rows: list[dict[str, str]] = []
    for raw in reader:
        normalized = {
            str(key).strip().upper(): str(value or "").strip() for key, value in raw.items()
        }
        row_series = normalized.get("SERIES", "")
        if series and row_series.upper() != series.upper():
            continue
        symbol = normalized.get("SYMBOL", "")
        legal_name = normalized.get("NAME OF COMPANY", "") or normalized.get(
            "NAME_OF_COMPANY", ""
        )
        isin = normalized.get("ISIN NUMBER", "") or normalized.get("ISIN_NUMBER", "")
        if not symbol or not legal_name or not isin:
            continue
        rows.append(
            {
                "symbol": symbol,
                "legal_name": legal_name,
                "isin": isin,
                "series": row_series,
                "date_of_listing": normalized.get("DATE OF LISTING", ""),
                "face_value": normalized.get("FACE VALUE", ""),
                "market_lot": normalized.get("MARKET LOT", ""),
            }
        )
    return rows


def validate_rows(rows: list[dict[str, str]], *, min_rows: int) -> dict[str, object]:
    if len(rows) < min_rows:
        raise ValueError(
            f"NSE security master contains only {len(rows)} rows; minimum expected is {min_rows}"
        )

    duplicate_symbols = _duplicates([row["symbol"] for row in rows])
    duplicate_isins = _duplicates([row["isin"] for row in rows])
    if duplicate_symbols or duplicate_isins:
        raise ValueError(
            "NSE security master contains duplicate identifiers: "
            f"symbols={duplicate_symbols[:10]}, isins={duplicate_isins[:10]}"
        )

    return {
        "row_count": len(rows),
        "unique_symbols": len({row["symbol"] for row in rows}),
        "unique_isins": len({row["isin"] for row in rows}),
    }


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        else:
            seen.add(value)
    return sorted(duplicates)


def _validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _ALLOWED_REMOTE_HOSTS:
        raise ValueError("Remote NSE security-master URL must be HTTPS on an official NSE host")


async def download_csv(url: str) -> str:
    _validate_remote_url(url)
    headers = {
        "User-Agent": "IndiaAIFinancialAnalyst/0.6 security-master-importer",
        "Accept": "text/csv,text/plain,*/*",
    }
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.text
    header = content.lstrip("\ufeff").splitlines()[0].upper() if content.strip() else ""
    if "SYMBOL" not in header or "ISIN" not in header:
        raise ValueError("Downloaded NSE security master does not contain the expected CSV header")
    return content


async def existing_coverage(database_url: str, *, series: str) -> int:
    engine = create_database_engine(database_url)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(
                text(
                    """
                    select count(*)
                    from securities
                    where primary_exchange = 'NSE'
                      and coalesce(metadata->>'nse_series', 'EQ') = :series
                    """
                ),
                {"series": series},
            )
            return int(value or 0)
    finally:
        await engine.dispose()


async def upsert_rows(
    rows: list[dict[str, str]],
    database_url: str,
    *,
    source_checksum: str,
) -> tuple[int, int]:
    engine = create_database_engine(database_url)
    security_sql = text(
        """
        insert into securities (
            legal_name, nse_symbol, isin, currency, primary_exchange, metadata
        ) values (
            :legal_name, :symbol, :isin, 'INR', 'NSE',
            jsonb_build_object(
                'nse_series', :series,
                'date_of_listing', :date_of_listing,
                'face_value', :face_value,
                'market_lot', :market_lot,
                'security_master_source', 'NSE EQUITY_L.csv',
                'security_master_sha256', :source_checksum
            )
        )
        on conflict (isin) do update set
            legal_name = excluded.legal_name,
            nse_symbol = excluded.nse_symbol,
            primary_exchange = coalesce(securities.primary_exchange, excluded.primary_exchange),
            metadata = securities.metadata || excluded.metadata,
            updated_at = now()
        returning id
        """
    )
    alias_sql = text(
        """
        insert into security_aliases (security_id, alias, alias_type, normalized_alias)
        values (:security_id, :alias, :alias_type, :normalized_alias)
        on conflict (security_id, normalized_alias) do update set alias = excluded.alias
        """
    )
    instrument_sql = text(
        """
        insert into provider_instruments (
            security_id, provider, instrument_id, exchange_segment, trading_symbol, metadata
        ) values (
            :security_id, 'nse', :instrument_id, 'NSE_EQ', :symbol,
            jsonb_build_object('series', :series, 'source_sha256', :source_checksum)
        )
        on conflict (provider, instrument_id) do update set
            security_id = excluded.security_id,
            trading_symbol = excluded.trading_symbol,
            metadata = excluded.metadata,
            updated_at = now()
        """
    )

    imported = 0
    aliases = 0
    try:
        async with engine.begin() as connection:
            for row in rows:
                payload = {**row, "source_checksum": source_checksum}
                result = await connection.execute(security_sql, payload)
                security_id = result.scalar_one()
                alias_values = {
                    row["symbol"],
                    row["legal_name"],
                    re.sub(
                        r"\b(limited|ltd)\.?$",
                        "",
                        row["legal_name"],
                        flags=re.IGNORECASE,
                    ).strip(),
                }
                for alias in alias_values:
                    normalized = normalize_alias(alias)
                    if not normalized:
                        continue
                    await connection.execute(
                        alias_sql,
                        {
                            "security_id": security_id,
                            "alias": alias,
                            "alias_type": (
                                "nse_symbol" if alias == row["symbol"] else "company_name"
                            ),
                            "normalized_alias": normalized,
                        },
                    )
                    aliases += 1
                await connection.execute(
                    instrument_sql,
                    {
                        "security_id": security_id,
                        "instrument_id": f"NSE:{row['symbol']}:{row['series']}",
                        "symbol": row["symbol"],
                        "series": row["series"],
                        "source_checksum": source_checksum,
                    },
                )
                imported += 1
    finally:
        await engine.dispose()
    return imported, aliases


async def main() -> None:
    parser = argparse.ArgumentParser(description="Import NSE equity security master")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--series", default="EQ")
    parser.add_argument("--file", help="Read an existing CSV instead of downloading")
    parser.add_argument("--min-rows", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.min_rows < 1:
        raise SystemExit("--min-rows must be >= 1")

    if args.file:
        content = Path(args.file).read_text(encoding="utf-8-sig")
        source = str(Path(args.file).resolve())
    else:
        content = await download_csv(args.url)
        source = args.url

    rows = parse_rows(content, series=args.series)
    validation = validate_rows(rows, min_rows=args.min_rows)
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

    summary: dict[str, object] = {
        "source": source,
        "series": args.series,
        "sha256": checksum,
        **validation,
        "dry_run": args.dry_run,
    }

    if args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL must be configured")

    before = await existing_coverage(settings.database_url, series=args.series)
    imported, aliases = await upsert_rows(
        rows,
        settings.database_url,
        source_checksum=checksum,
    )
    after = await existing_coverage(settings.database_url, series=args.series)
    summary.update(
        {
            "database_rows_before": before,
            "database_rows_after": after,
            "imported_or_updated": imported,
            "aliases_processed": aliases,
        }
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
