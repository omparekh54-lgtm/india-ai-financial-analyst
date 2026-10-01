"""Validate original NSE quote exports; never construct missing classification labels."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.connectors.nse_classification import (
    NseIndustryClassification,
    parse_nse_quote_classification,
)


def load_classification_snapshot(
    manifest_path: Path,
    expected: dict[str, str],
    *,
    approval_reference: str,
    as_of: datetime | None = None,
) -> tuple[dict[str, NseIndustryClassification], dict[str, dict[str, str]]]:
    """Require exact target coverage, reviewed origin, raw checksums and current exports.

    A checksum establishes integrity, not authenticity. The approval reference records
    the operator's separate review of original official responses before production use.
    """
    if not approval_reference.strip() or any(
        term in approval_reference.lower()
        for term in ("synthetic", "mock", "fake", "dummy", "fixture", "sample",
                     "generated", "placeholder")
    ):
        raise ValueError("A genuine export-origin review reference is required")
    root = manifest_path.resolve().parent
    if manifest_path.stat().st_size > 256_000:
        raise ValueError("Classification manifest is too large")
    manifest = json.loads(manifest_path.read_bytes())
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Unsupported classification manifest")
    records = manifest.get("responses")
    if not isinstance(records, list) or len(records) != len(expected) or len(records) > 50:
        raise ValueError("Manifest must cover the exact prepared universe")
    now = as_of or datetime.now(UTC)
    results: dict[str, NseIndustryClassification] = {}
    evidence: dict[str, dict[str, str]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise TypeError("Each manifest response must be an object")
        symbol = record.get("symbol")
        if symbol not in expected or symbol in results:
            raise ValueError("Unexpected or duplicate classification symbol")
        filename = record.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("Response file must be a filename inside the export directory")
        path = (root / filename).resolve()
        if path.parent != root or path.stat().st_size > 1_000_000:
            raise ValueError("Response file is outside the export directory or too large")
        raw = path.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        if record.get("sha256") != checksum:
            raise ValueError(f"Raw response checksum mismatch for {symbol}")
        timestamp = record.get("retrieved_at")
        if not isinstance(timestamp, str):
            raise TypeError("Export retrieval timestamp is required")
        retrieved = datetime.fromisoformat(timestamp)
        if retrieved.tzinfo is None or not now - timedelta(days=30) <= retrieved <= now:
            raise ValueError("Export retrieval time must be timezone-aware and within 30 days")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("info"), dict):
            raise TypeError("Original NSE quote response is required")
        if payload["info"].get("symbol") != symbol:
            raise ValueError("An explicit matching symbol is required in the response")
        classification = parse_nse_quote_classification(
            payload, expected_symbol=symbol, expected_isin=expected[symbol],
        )
        # Do not accept another provider, an unofficial mirror or a renamed taxonomy.
        if record.get("source_uri") != classification.source_uri:
            raise ValueError("Source URI must identify the exact official NSE quote request")
        results[symbol] = classification
        evidence[symbol] = {
            "raw_response_sha256": checksum,
            "retrieved_at": retrieved.astimezone(UTC).isoformat(),
            "export_origin_review_reference": approval_reference.strip(),
            "collection_method": "reviewed_original_nse_quote_export",
        }
    return results, evidence
