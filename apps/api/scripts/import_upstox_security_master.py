from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import text

from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.reference_provenance import (
    upsert_reference_source,
    validate_reference_approval,
    validate_source_uri,
)

DEFAULT_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
DEFAULT_APPROVAL_REFERENCE = "SG-2026-08-31-01"
_ALLOWED_REMOTE_HOSTS = {"assets.upstox.com"}
_NONPRODUCTION_TOKEN = re.compile(
    r"(^|[^a-z0-9])(test|dummy|mock|fake|sample|placeholder)([^a-z0-9]|$)",
    re.IGNORECASE,
)


def normalize_alias(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def parse_payload(content: bytes) -> list[dict[str, Any]]:
    raw = gzip.decompress(content) if content[:2] == b"\x1f\x8b" else content
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Upstox instrument artifact is not valid UTF-8 JSON") from exc

    if isinstance(payload, dict):
        payload = payload.get("data")
    if not isinstance(payload, list):
        raise TypeError("Upstox instrument artifact must contain a JSON array")

    rows: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def parse_equity_rows(payload: list[dict[str, Any]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for raw in payload:
        segment = str(raw.get("segment") or "").strip().upper()
        exchange = str(raw.get("exchange") or "").strip().upper()
        instrument_type = str(raw.get("instrument_type") or "").strip().upper()
        if segment != "NSE_EQ" or exchange != "NSE" or instrument_type != "EQ":
            continue

        symbol = str(raw.get("trading_symbol") or raw.get("tradingsymbol") or "").strip().upper()
        legal_name = str(raw.get("name") or raw.get("short_name") or "").strip()
        isin = str(raw.get("isin") or "").strip().upper()
        instrument_key = str(raw.get("instrument_key") or "").strip()
        combined = f"{symbol} {legal_name} {isin} {instrument_key}"
        if not symbol or not legal_name or not isin or not instrument_key:
            continue
        if _NONPRODUCTION_TOKEN.search(combined):
            continue
        if re.fullmatch(r"IN[A-Z0-9]{10}", isin) is None:
            continue

        rows.append(
            {
                "symbol": symbol,
                "legal_name": legal_name,
                "isin": isin,
                "instrument_key": instrument_key,
                "exchange_token": str(raw.get("exchange_token") or "").strip(),
                "security_type": str(raw.get("security_type") or "").strip(),
                "short_name": str(raw.get("short_name") or "").strip(),
                "lot_size": raw.get("lot_size"),
                "tick_size": raw.get("tick_size"),
                "mtf_enabled": raw.get("mtf_enabled"),
            }
        )
    return rows


def validate_rows(rows: list[dict[str, object]], *, min_rows: int) -> dict[str, object]:
    if min_rows < 1:
        raise ValueError("minimum security-master rows must be >= 1")
    if len(rows) < min_rows:
        raise ValueError(
            f"Upstox NSE equity master contains only {len(rows)} rows; minimum expected is {min_rows}"
        )

    duplicate_symbols = _duplicates(str(row["symbol"]) for row in rows)
    duplicate_isins = _duplicates(str(row["isin"]) for row in rows)
    duplicate_keys = _duplicates(str(row["instrument_key"]) for row in rows)
    if duplicate_symbols or duplicate_isins or duplicate_keys:
        raise ValueError(
            "Upstox NSE equity master contains duplicate identifiers: "
            f"symbols={duplicate_symbols[:10]}, isins={duplicate_isins[:10]}, "
            f"instrument_keys={duplicate_keys[:10]}"
        )

    return {
        "row_count": len(rows),
        "unique_symbols": len({str(row["symbol"]) for row in rows}),
        "unique_isins": len({str(row["isin"]) for row in rows}),
        "unique_instrument_keys": len({str(row["instrument_key"]) for row in rows}),
    }


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        else:
            seen.add(value)
    return sorted(duplicates)


def _validate_remote_url(url: str) -> str:
    cleaned = validate_source_uri(url)
    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _ALLOWED_REMOTE_HOSTS:
        raise ValueError("Upstox security-master URL must be HTTPS on assets.upstox.com")
    return cleaned


async def download_artifact(url: str) -> bytes:
    source_url = _validate_remote_url(url)
    headers = {
        "User-Agent": "IndiaAIFinancialAnalyst/0.6 security-master-importer",
        "Accept": "application/gzip,application/json,*/*",
    }
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(source_url)
        response.raise_for_status()
        content = response.content
    if not content:
        raise ValueError("Downloaded Upstox instrument artifact is empty")
    return content


async def existing_coverage(database_url: str) -> int:
    engine = create_database_engine(database_url)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(
                text(
                    """
                    select count(*)
                    from securities
                    where primary_exchange = 'NSE'
                      and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
                    """
                )
            )
            return int(value or 0)
    finally:
        await engine.dispose()


async def upsert_rows(
    rows: list[dict[str, object]],
    database_url: str,
    *,
    source_id: UUID,
    source_checksum: str,
    source_url: str,
    approval_reference: str,
) -> tuple[int, int]:
    engine = create_database_engine(database_url)
    security_sql = text(
        """
        insert into securities (
            legal_name, nse_symbol, isin, currency, primary_exchange, metadata
        ) values (
            :legal_name, :symbol, :isin, 'INR', 'NSE',
            jsonb_build_object(
                'nse_series', 'EQ',
                'security_master_source', 'Upstox BOD NSE instruments',
                'security_master_source_url', :source_url,
                'security_master_sha256', :source_checksum,
                'security_master_source_id', :source_id,
                'security_master_provenance_class', 'licensed_or_approved',
                'security_master_approval_reference', :approval_reference,
                'upstox_instrument_key', :instrument_key,
                'upstox_exchange_token', :exchange_token,
                'upstox_security_type', :security_type,
                'upstox_lot_size', :lot_size,
                'upstox_tick_size', :tick_size,
                'upstox_mtf_enabled', :mtf_enabled
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
            :security_id, 'upstox', :instrument_key, 'NSE_EQ', :symbol,
            jsonb_build_object(
                'exchange_token', :exchange_token,
                'security_type', :security_type,
                'source_url', :source_url,
                'source_sha256', :source_checksum,
                'source_id', :source_id,
                'provenance_class', 'licensed_or_approved',
                'approval_reference', :approval_reference
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

    imported = 0
    aliases = 0
    try:
        async with engine.begin() as connection:
            for row in rows:
                payload = {
                    **row,
                    "source_id": source_id,
                    "source_checksum": source_checksum,
                    "source_url": source_url,
                    "approval_reference": approval_reference,
                }
                result = await connection.execute(security_sql, payload)
                security_id = result.scalar_one()
                alias_values = {
                    str(row["symbol"]),
                    str(row["legal_name"]),
                    str(row.get("short_name") or ""),
                    re.sub(
                        r"\b(limited|ltd)\.?$",
                        "",
                        str(row["legal_name"]),
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
                                "nse_symbol" if alias == str(row["symbol"]) else "company_name"
                            ),
                            "normalized_alias": normalized,
                        },
                    )
                    aliases += 1
                await connection.execute(instrument_sql, {**payload, "security_id": security_id})
                imported += 1
    finally:
        await engine.dispose()
    return imported, aliases


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import real NSE cash-equity reference data from the documented Upstox BOD "
            "instrument artifact. This is a broker-reference fallback, not an NSE-primary file."
        )
    )
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--file", help="Read an already-downloaded Upstox NSE JSON/JSON.GZ artifact")
    parser.add_argument("--min-rows", type=int, default=1000)
    parser.add_argument("--approval-reference", default=DEFAULT_APPROVAL_REFERENCE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_url = _validate_remote_url(args.url)
    approval = validate_reference_approval(source_url, args.approval_reference)
    if approval.provenance_class != "licensed_or_approved":
        raise SystemExit("Upstox fallback must remain classified as licensed_or_approved")

    if args.file:
        file_path = Path(args.file)
        content = file_path.read_bytes()
        input_location = str(file_path.resolve())
    else:
        content = await download_artifact(source_url)
        input_location = source_url

    payload = parse_payload(content)
    rows = parse_equity_rows(payload)
    validation = validate_rows(rows, min_rows=args.min_rows)
    checksum = hashlib.sha256(content).hexdigest()
    summary: dict[str, object] = {
        "input_location": input_location,
        "source_url": source_url,
        "provenance_class": approval.provenance_class,
        "approval_reference": approval.approval_reference,
        "sha256": checksum,
        **validation,
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL must be configured")

    before = await existing_coverage(settings.database_url)
    source_engine = create_database_engine(settings.database_url)
    try:
        source_id = await upsert_reference_source(
            source_engine,
            security_id=None,
            source_type="security_master",
            source_uri=source_url,
            title="Upstox BOD NSE cash-equity instrument master",
            published_at=None,
            checksum=checksum,
            metadata={
                "artifact_kind": "broker_instrument_master",
                "exchange": "NSE",
                "segment": "NSE_EQ",
                "instrument_type": "EQ",
                "row_count": len(rows),
                "governance_record": DEFAULT_APPROVAL_REFERENCE,
            },
            approval_reference=args.approval_reference,
        )
    finally:
        await source_engine.dispose()

    imported, aliases = await upsert_rows(
        rows,
        settings.database_url,
        source_id=source_id,
        source_checksum=checksum,
        source_url=source_url,
        approval_reference=args.approval_reference,
    )
    after = await existing_coverage(settings.database_url)
    summary.update(
        {
            "source_id": str(source_id),
            "database_rows_before": before,
            "database_rows_after": after,
            "imported_or_updated": imported,
            "aliases_processed": aliases,
        }
    )
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
