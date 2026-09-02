from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.reference_provenance import (
    upsert_reference_source,
    validate_reference_approval,
    validate_source_uri,
)

DEFAULT_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
DEFAULT_APPROVAL_REFERENCE = "SG-2026-08-31-01"
_ALLOWED_HOST = "assets.upstox.com"


@dataclass(frozen=True)
class UpstoxMappingRow:
    isin: str
    trading_symbol: str
    instrument_key: str
    exchange_token: str
    security_type: str


@dataclass(frozen=True)
class CanonicalSecurity:
    security_id: UUID
    isin: str
    nse_symbol: str


def parse_mapping_rows(content: bytes) -> list[UpstoxMappingRow]:
    raw = gzip.decompress(content) if content[:2] == b"\x1f\x8b" else content
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Upstox instrument artifact is not valid UTF-8 JSON") from exc
    if isinstance(payload, dict):
        payload = payload.get("data")
    if not isinstance(payload, list):
        raise TypeError("Upstox instrument artifact must contain a JSON array")

    rows: list[UpstoxMappingRow] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if str(item.get("segment") or "").strip().upper() != "NSE_EQ":
            continue
        if str(item.get("exchange") or "").strip().upper() != "NSE":
            continue
        if str(item.get("instrument_type") or "").strip().upper() != "EQ":
            continue
        isin = str(item.get("isin") or "").strip().upper()
        symbol = str(item.get("trading_symbol") or item.get("tradingsymbol") or "").strip().upper()
        instrument_key = str(item.get("instrument_key") or "").strip()
        if not isin or not symbol or not instrument_key:
            continue
        rows.append(
            UpstoxMappingRow(
                isin=isin,
                trading_symbol=symbol,
                instrument_key=instrument_key,
                exchange_token=str(item.get("exchange_token") or "").strip(),
                security_type=str(item.get("security_type") or "").strip(),
            )
        )
    return rows


def validate_mapping_rows(rows: list[UpstoxMappingRow], *, min_rows: int) -> None:
    if min_rows < 1000:
        raise ValueError("min_rows must be >= 1000 for production NSE mapping")
    if len(rows) < min_rows:
        raise ValueError(
            f"Upstox NSE mapping artifact contains only {len(rows)} rows; minimum is {min_rows}"
        )
    isins = [row.isin for row in rows]
    keys = [row.instrument_key for row in rows]
    duplicate_isins = _duplicates(isins)
    duplicate_keys = _duplicates(keys)
    if duplicate_isins or duplicate_keys:
        raise ValueError(
            "Upstox mapping artifact contains duplicate identifiers: "
            f"isins={duplicate_isins[:10]}, instrument_keys={duplicate_keys[:10]}"
        )


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _validate_url(value: str) -> str:
    cleaned = validate_source_uri(value)
    parsed = urlparse(cleaned)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != _ALLOWED_HOST:
        raise ValueError("Upstox mapping URL must be HTTPS on assets.upstox.com")
    return cleaned


async def download_artifact(url: str) -> bytes:
    source_url = _validate_url(url)
    async with httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
        headers={
            "User-Agent": "IndiaAIFinancialAnalyst/0.7 mapping-importer",
            "Accept": "application/gzip,application/json,*/*",
        },
    ) as client:
        response = await client.get(source_url)
        response.raise_for_status()
        if not response.content:
            raise ValueError("Downloaded Upstox instrument artifact is empty")
        return response.content


async def load_canonical_universe(engine: AsyncEngine) -> list[CanonicalSecurity]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    select id, isin, nse_symbol
                    from securities
                    where primary_exchange = 'NSE'
                      and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
                      and isin is not null
                      and nse_symbol is not null
                    order by nse_symbol
                    """
                )
            )
        ).mappings().all()
    return [
        CanonicalSecurity(
            security_id=UUID(str(row["id"])),
            isin=str(row["isin"]).upper(),
            nse_symbol=str(row["nse_symbol"]).upper(),
        )
        for row in rows
    ]


def match_mappings(
    canonical: list[CanonicalSecurity],
    rows: list[UpstoxMappingRow],
) -> tuple[list[tuple[CanonicalSecurity, UpstoxMappingRow]], list[CanonicalSecurity]]:
    by_isin = {row.isin: row for row in rows}
    matched: list[tuple[CanonicalSecurity, UpstoxMappingRow]] = []
    missing: list[CanonicalSecurity] = []
    for security in canonical:
        row = by_isin.get(security.isin)
        if row is None:
            missing.append(security)
        else:
            matched.append((security, row))
    return matched, missing


async def persist_mappings(
    engine: AsyncEngine,
    *,
    mappings: list[tuple[CanonicalSecurity, UpstoxMappingRow]],
    source_id: UUID,
    source_url: str,
    checksum: str,
    approval_reference: str,
) -> int:
    statement = text(
        """
        insert into provider_instruments (
          security_id, provider, instrument_id, exchange_segment, trading_symbol, metadata
        ) values (
          :security_id, 'upstox', :instrument_key, 'NSE_EQ', :trading_symbol,
          jsonb_build_object(
            'canonical_isin', :isin,
            'canonical_nse_symbol', :canonical_symbol,
            'exchange_token', :exchange_token,
            'security_type', :security_type,
            'source_url', :source_url,
            'source_sha256', :checksum,
            'source_id', cast(:source_id as text),
            'provenance_class', 'licensed_or_approved',
            'approval_reference', :approval_reference,
            'mapping_method', 'isin_exact'
          )
        )
        on conflict (provider, instrument_id) do update set
          security_id = excluded.security_id,
          exchange_segment = excluded.exchange_segment,
          trading_symbol = excluded.trading_symbol,
          metadata = excluded.metadata,
          updated_at = now()
        """
    )
    async with engine.begin() as connection:
        for security, row in mappings:
            await connection.execute(
                statement,
                {
                    "security_id": security.security_id,
                    "instrument_key": row.instrument_key,
                    "trading_symbol": row.trading_symbol,
                    "isin": security.isin,
                    "canonical_symbol": security.nse_symbol,
                    "exchange_token": row.exchange_token,
                    "security_type": row.security_type,
                    "source_url": source_url,
                    "checksum": checksum,
                    "source_id": source_id,
                    "approval_reference": approval_reference,
                },
            )
    return len(mappings)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Attach Upstox NSE_EQ instrument keys to the existing canonical NSE universe by exact "
            "ISIN match. This mapping-only path never creates or mutates securities."
        )
    )
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--file")
    parser.add_argument("--min-rows", type=int, default=1000)
    parser.add_argument("--min-coverage-pct", type=float, default=100.0)
    parser.add_argument("--approval-reference", default=DEFAULT_APPROVAL_REFERENCE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not 0 < args.min_coverage_pct <= 100:
        parser.error("--min-coverage-pct must be > 0 and <= 100")
    source_url = _validate_url(args.url)
    approval = validate_reference_approval(source_url, args.approval_reference)
    if approval.provenance_class != "licensed_or_approved":
        raise SystemExit("Upstox mapping must remain licensed_or_approved")

    if args.file:
        content = Path(args.file).read_bytes()
        input_location = str(Path(args.file).resolve())
    else:
        content = await download_artifact(source_url)
        input_location = source_url
    rows = parse_mapping_rows(content)
    validate_mapping_rows(rows, min_rows=args.min_rows)
    checksum = hashlib.sha256(content).hexdigest()

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        canonical = await load_canonical_universe(engine)
        if len(canonical) < args.min_rows:
            raise SystemExit(
                "Canonical NSE universe is below the production threshold: "
                f"{len(canonical)} < {args.min_rows}"
            )
        matched, missing = match_mappings(canonical, rows)
        coverage_pct = (len(matched) / len(canonical)) * 100.0 if canonical else 0.0
        symbol_mismatches = [
            {
                "isin": security.isin,
                "canonical_symbol": security.nse_symbol,
                "upstox_symbol": row.trading_symbol,
            }
            for security, row in matched
            if security.nse_symbol != row.trading_symbol
        ]
        summary: dict[str, object] = {
            "data_policy": "canonical_nse_master_plus_approved_broker_mapping",
            "input_location": input_location,
            "source_url": source_url,
            "sha256": checksum,
            "canonical_nse_securities": len(canonical),
            "upstox_nse_eq_rows": len(rows),
            "matched": len(matched),
            "missing": len(missing),
            "coverage_pct": round(coverage_pct, 4),
            "required_coverage_pct": args.min_coverage_pct,
            "missing_preview": [item.nse_symbol for item in missing[:50]],
            "symbol_mismatch_count": len(symbol_mismatches),
            "symbol_mismatch_preview": symbol_mismatches[:50],
            "dry_run": args.dry_run,
            "securities_mutated": False,
        }
        if coverage_pct < args.min_coverage_pct:
            summary["blocked_reason"] = "Upstox ISIN mapping coverage is below the configured threshold"
            print(json.dumps(summary, indent=2, sort_keys=True, default=str))
            return 2
        if args.dry_run:
            print(json.dumps(summary, indent=2, sort_keys=True, default=str))
            return 0

        source_id = await upsert_reference_source(
            engine,
            security_id=None,
            source_type="broker_instrument_master",
            source_uri=source_url,
            title="Upstox BOD NSE instrument mapping artifact",
            published_at=None,
            checksum=checksum,
            metadata={
                "provider": "upstox",
                "mapping_method": "isin_exact",
                "canonical_source": "NSE",
                "row_count": len(rows),
                "matched_count": len(matched),
            },
            approval_reference=args.approval_reference,
        )
        written = await persist_mappings(
            engine,
            mappings=matched,
            source_id=source_id,
            source_url=source_url,
            checksum=checksum,
            approval_reference=args.approval_reference,
        )
        summary["source_id"] = str(source_id)
        summary["mappings_written"] = written
    finally:
        await engine.dispose()

    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
