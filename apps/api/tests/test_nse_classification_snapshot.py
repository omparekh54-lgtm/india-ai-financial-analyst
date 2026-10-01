from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.connectors.nse_classification_snapshot import load_classification_snapshot

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def export(tmp_path):
    payload = {
        "info": {"symbol": "HDFCBANK", "isin": "INE040A01034"},
        "industryInfo": {"macro": "Financial Services", "sector": "Financial Services",
                         "industry": "Banks", "basicIndustry": "Private Sector Bank"},
    }
    raw = json.dumps(payload).encode()
    (tmp_path / "HDFCBANK.json").write_bytes(raw)
    record = {"symbol": "HDFCBANK", "file": "HDFCBANK.json",
              "source_uri": "https://www.nseindia.com/api/quote-equity?symbol=HDFCBANK",
              "sha256": hashlib.sha256(raw).hexdigest(),
              "retrieved_at": "2026-09-30T12:00:00Z"}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "responses": [record]}))
    return manifest, record, payload


def load(manifest, **kwargs):
    return load_classification_snapshot(
        manifest, {"HDFCBANK": "INE040A01034"},
        approval_reference="operator-review:official-export", as_of=NOW, **kwargs,
    )


def test_preserves_original_response_checksum_and_retrieval_time(tmp_path):
    manifest, record, _ = export(tmp_path)
    classifications, evidence = load(manifest)
    assert classifications["HDFCBANK"].basic_industry == "Private Sector Bank"
    assert evidence["HDFCBANK"]["raw_response_sha256"] == record["sha256"]
    assert evidence["HDFCBANK"]["retrieved_at"] == "2026-09-30T12:00:00+00:00"


@pytest.mark.parametrize(("key", "value", "error"), [
    ("sha256", "0" * 64, "checksum mismatch"),
    ("source_uri", "https://example.com/quote?symbol=HDFCBANK", "Source URI"),
    ("symbol", "SBIN", "Unexpected"),
    ("file", "../outside.json", "inside the export"),
    ("retrieved_at", "2026-09-30T12:00:00", "timezone-aware"),
    ("retrieved_at", "2026-10-02T12:00:00Z", "within 30 days"),
    ("retrieved_at", "2025-09-30T12:00:00Z", "within 30 days"),
])
def test_rejects_invalid_origin_identity_integrity_and_dates(tmp_path, key, value, error):
    manifest, record, _ = export(tmp_path)
    record[key] = value
    manifest.write_text(json.dumps({"schema_version": 1, "responses": [record]}))
    with pytest.raises(ValueError, match=error):
        load(manifest)


@pytest.mark.parametrize("change", ["isin", "symbol", "basicIndustry"])
def test_rejects_mismatched_identity_or_missing_labels_even_with_valid_checksum(tmp_path, change):
    manifest, record, payload = export(tmp_path)
    if change == "basicIndustry":
        del payload["industryInfo"][change]
    else:
        del payload["info"][change]
    raw = json.dumps(payload).encode()
    (tmp_path / record["file"]).write_bytes(raw)
    record["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest.write_text(json.dumps({"schema_version": 1, "responses": [record]}))
    with pytest.raises(ValueError):
        load(manifest)


def test_requires_exact_coverage_and_separate_origin_review(tmp_path):
    manifest, _, _ = export(tmp_path)
    with pytest.raises(ValueError, match="exact prepared universe"):
        load_classification_snapshot(manifest, {}, approval_reference="operator-review", as_of=NOW)
    with pytest.raises(ValueError, match="review reference"):
        load_classification_snapshot(manifest, {}, approval_reference="", as_of=NOW)


@pytest.mark.asyncio
async def test_persistence_links_original_checksum_and_actual_collection_time(tmp_path, monkeypatch):
    from scripts import backfill_nse_industry_classification as importer

    manifest, record, _ = export(tmp_path)
    classifications, evidence = load(manifest)
    connection = AsyncMock()
    connection.scalar.return_value = uuid4()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = context
    engine.dispose = AsyncMock()
    monkeypatch.setattr(importer, "create_database_engine", lambda _: engine)
    security_id = uuid4()
    updated = await importer.persist_classifications(
        "isolated-test", {security_id: classifications["HDFCBANK"]},
        export_evidence={security_id: evidence["HDFCBANK"]},
    )
    assert updated == 1
    source_params = connection.execute.await_args_list[0].args[1]
    security_params = connection.execute.await_args_list[1].args[1]
    assert source_params["checksum"] == security_params["checksum"] == record["sha256"]
    assert source_params["retrieved_at"] == security_params["retrieved_at"]
    assert source_params["retrieved_at"] == "2026-09-30T12:00:00+00:00"
    assert json.loads(source_params["metadata"])["raw_response_sha256"] == record["sha256"]
