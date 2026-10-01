"""Recover two missing LT quarters from a reviewed, checksum-pinned issuer filing."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pymupdf
from sqlalchemy import text

from app.core.config import get_settings
from app.core.financial_history_coverage import load_financial_history_coverage
from app.core.prepared_security_readiness import refresh_prepared_security_readiness
from app.db import create_database_engine
from app.ingestion.financials import FinancialFactIngestor, RawFinancialFact
from app.ingestion.reference_provenance import resolve_security, upsert_reference_source

SOURCE_URL = (
    "https://investors.larsentoubro.com/upload/Quarterly/"
    "FY2025QuarterlyFY2025%20Quarterly%20Financial%20Result%20Dec%202024.pdf"
)
DOCUMENT_SHA256 = "7285dfb8b33c97c0380e1e34b58ea338df12544c1ec178e4e33b3ae2f876e8b7"
LT_ISIN = "INE018A01030"
LT_CIN = "L99999MH1946PLC004768"
MAX_BYTES = 5_000_000
PERIODS = ((date(2024, 10, 1), date(2024, 12, 31)),
           (date(2024, 7, 1), date(2024, 9, 30)))
ROWS = (
    ("revenue", r"a\) Revenue from operations"),
    ("other_income", r"b\) Other income \(net\)"),
    ("total_income", r"Total Income"),
    ("employee_cost", r"c\) Employee benefits expense"),
    ("total_expenses", r"Total Expenses"),
    ("pbt", r"5 Profit before tax \(3\+4\)"),
    ("tax_expense", r"Total tax expense"),
    ("pat", r"9 Net profit after tax and share in profit/\(loss\) of joint ventures/associates \(7\+8\)"),
    ("eps_basic", r"\(a\) Basic EPS \( \)"),
    ("eps_diluted", r"\(b\) Diluted EPS \( \)"),
)


def parse_reviewed_table(page_text: str) -> list[RawFinancialFact]:
    lines = [" ".join(line.split()) for line in page_text.splitlines() if line.strip()]
    joined = "\n".join(lines)
    for required in (
        "LARSEN & TOUBRO LIMITED", f"CIN: {LT_CIN}",
        "STATEMENT OF CONSOLIDATED UNAUDITED FINANCIAL RESULTS",
        "FOR THE QUARTER AND NINE MONTHS ENDED DECEMBER 31, 2024", "Crore",
        "December 31, September 30, December 31, December 31, December 31, March 31,",
        "2024 2024 2023 2024 2023 2024",
    ):
        if required not in joined:
            raise ValueError("Reviewed issuer/table identity or column header mismatch")
    output = []
    for name, label in ROWS:
        matches = [re.fullmatch(label + r"\s+((?:\d+\.\d{2}\s+){5}\d+\.\d{2})", line)
                   for line in lines]
        matched = [m for m in matches if m is not None]
        if len(matched) != 1:
            raise ValueError(f"Ambiguous or malformed consolidated row: {name}")
        values = matched[0].group(1).split()
        for column, (start, end) in enumerate(PERIODS):
            output.append(RawFinancialFact(
                name=name, period_start=start, period_end=end, period_type="quarterly",
                value=Decimal(values[column]),
                unit="INR/share" if name.startswith("eps_") else "INR crore",
                metadata={"source_format": "issuer_pdf_table", "issuer_cin": LT_CIN,
                          "consolidation": "consolidated", "document_sha256": DOCUMENT_SHA256,
                          "page_number": 1, "table_column": column + 1,
                          "reported_row": matched[0].group(0)},
            ))
    by_period = {end: {f.name: f.value for f in output if f.period_end == end}
                 for _, end in PERIODS}
    for values in by_period.values():
        if (values["revenue"] + values["other_income"] != values["total_income"]
                or values["total_income"] - values["total_expenses"] != values["pbt"]):
            raise ValueError("Reported income/expense totals do not reconcile")
    return output


def parse_reviewed_pdf(content: bytes) -> list[RawFinancialFact]:
    if (len(content) > MAX_BYTES or not content.startswith(b"%PDF")
            or hashlib.sha256(content).hexdigest() != DOCUMENT_SHA256):
        raise ValueError("Issuer filing differs from the reviewed PDF checksum")
    with pymupdf.open(stream=content, filetype="pdf") as document:
        if len(document) != 7:
            raise ValueError("Reviewed issuer filing page count mismatch")
        return parse_reviewed_table(document[0].get_text(sort=True))


async def download() -> bytes:
    async with (
        httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client,
        client.stream("GET", SOURCE_URL) as response,
    ):
        response.raise_for_status()
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > MAX_BYTES:
                raise ValueError("Issuer filing exceeds the bounded size limit")
    return bytes(content)


async def recover(*, dry_run: bool, approval_reference: str) -> dict[str, object]:
    if not approval_reference.strip():
        raise ValueError("A reviewed issuer-source governance record is required")
    facts = parse_reviewed_pdf(await download())
    summary: dict[str, object] = {"symbol": "LT", "dry_run": dry_run,
        "source_uri": SOURCE_URL, "document_sha256": DOCUMENT_SHA256,
        "facts": len(facts), "periods": [end.isoformat() for _, end in PERIODS]}
    if dry_run:
        return summary
    settings = get_settings()
    if not settings.database_url:
        raise ValueError("Existing database configuration is required")
    engine = create_database_engine(settings.database_url)
    try:
        security_id, _ = await resolve_security(engine, "LT")
        async with engine.connect() as connection:
            isin = await connection.scalar(text("select isin from securities where id=:id"),
                                           {"id": security_id})
            size = await connection.scalar(text("select pg_database_size(current_database())"))
        if isin != LT_ISIN or size is None or size >= 450_000_000:
            raise ValueError("Canonical issuer or free-tier storage guard failed")
        source_id = await upsert_reference_source(
            engine, security_id=security_id, source_type="reference_financials",
            source_uri=SOURCE_URL, title="LT consolidated results: December and September 2024",
            published_at=datetime(2025, 1, 30, tzinfo=UTC), checksum=DOCUMENT_SHA256,
            approval_reference=approval_reference,
            metadata={"importer": "recover_lt_issuer_history", "issuer_cin": LT_CIN,
                      "verified_isin": LT_ISIN, "document_sha256": DOCUMENT_SHA256,
                      "source_authority": "issuer_investor_relations",
                      "consolidation": "consolidated", "page_number": 1},
        )
        summary["ingestion"] = await FinancialFactIngestor(engine).ingest_batch(
            security_id=security_id, source_id=source_id, facts=facts)
        async with engine.begin() as connection:
            for index, (_, end) in enumerate(PERIODS):
                content = "\n".join([
                    f"LT consolidated issuer filing | period_end={end} | quarterly | PDF page 1",
                    *(f"{f.name}: {f.value} {f.unit} | column={f.metadata['table_column']}"
                      for f in facts if f.period_end == end),
                ])
                await connection.execute(text("""
                    insert into evidence_chunks
                      (source_id,chunk_index,page_number,section,content,embedding,metadata)
                    values (:source_id,:index,1,'issuer_financial_results',:content,null,
                            cast(:metadata as jsonb))
                    on conflict (source_id,chunk_index) do update set
                      content=excluded.content,
                      embedding=case when evidence_chunks.content=excluded.content
                                     then evidence_chunks.embedding else null end,
                      metadata=case when evidence_chunks.content=excluded.content
                                    then evidence_chunks.metadata || excluded.metadata
                                    else excluded.metadata end
                """), {"source_id": source_id, "index": index, "content": content,
                         "metadata": json.dumps({"ai_assisted": False,
                             "evidence_kind": "deterministic_issuer_pdf_table",
                             "document_sha256": DOCUMENT_SHA256, "period_end": str(end),
                             "period_type": "quarterly", "fact_count": 10,
                             "content_sha256": hashlib.sha256(content.encode()).hexdigest()})})
        summary["evidence_chunks"] = 2
        history = await load_financial_history_coverage(engine, security_id=security_id)
        readiness = await refresh_prepared_security_readiness(engine, security_id, settings)
        summary.update(source_id=str(source_id), financial_history_complete=history.complete,
                       history=history.as_dict(), readiness=readiness.as_dict())
        return summary
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--approval-reference", required=True)
    args = parser.parse_args()
    result = asyncio.run(recover(dry_run=args.dry_run, approval_reference=args.approval_reference))
    print(json.dumps(result, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
