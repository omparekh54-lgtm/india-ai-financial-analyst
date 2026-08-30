from __future__ import annotations

import argparse
import asyncio
import csv
import io
import re
import sys
from pathlib import Path

import httpx
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.db import create_database_engine  # noqa: E402


DEFAULT_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"


def normalize_alias(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def parse_rows(content: str, *, series: str = "EQ") -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    rows: list[dict[str, str]] = []
    for raw in reader:
        normalized = {str(key).strip().upper(): str(value or "").strip() for key, value in raw.items()}
        row_series = normalized.get("SERIES", "")
        if series and row_series.upper() != series.upper():
            continue
        symbol = normalized.get("SYMBOL", "")
        legal_name = normalized.get("NAME OF COMPANY", "") or normalized.get("NAME_OF_COMPANY", "")
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


async def download_csv(url: str) -> str:
    headers = {
        "User-Agent": "IndiaAIFinancialAnalyst/0.1 security-master-importer",
        "Accept": "text/csv,text/plain,*/*",
    }
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def upsert_rows(rows: list[dict[str, str]], database_url: str) -> tuple[int, int]:
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
                'security_master_source', 'NSE EQUITY_L.csv'
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
            jsonb_build_object('series', :series)
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
    async with engine.begin() as connection:
        for row in rows:
            result = await connection.execute(security_sql, row)
            security_id = result.scalar_one()
            alias_values = {
                row["symbol"],
                row["legal_name"],
                re.sub(r"\b(limited|ltd)\.?$", "", row["legal_name"], flags=re.IGNORECASE).strip(),
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
                        "alias_type": "nse_symbol" if alias == row["symbol"] else "company_name",
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
                },
            )
            imported += 1

    await engine.dispose()
    return imported, aliases


async def main() -> None:
    parser = argparse.ArgumentParser(description="Import NSE equity security master")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--series", default="EQ")
    parser.add_argument("--file", help="Read an existing CSV instead of downloading")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL must be configured")

    if args.file:
        content = Path(args.file).read_text(encoding="utf-8-sig")
    else:
        content = await download_csv(args.url)

    rows = parse_rows(content, series=args.series)
    imported, aliases = await upsert_rows(rows, settings.database_url)
    print(f"Imported/updated {imported} NSE securities and {aliases} aliases")


if __name__ == "__main__":
    asyncio.run(main())
