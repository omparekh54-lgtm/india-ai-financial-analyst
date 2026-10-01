"""Import reviewed insurer share inputs and recalculate only the eleven remaining stocks."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from lxml import etree
from sqlalchemy import text

from app.core.config import get_settings
from app.core.prepared_security_readiness import refresh_prepared_security_readiness
from app.db import create_database_engine
from app.ingestion.derived_metric_ingestion import DerivedSecurityMetricIngestor
from app.ingestion.derived_metrics import derive_peer_metrics, partition_metric_bundle
from app.ingestion.financials import FinancialFactIngestor, RawFinancialFact
from app.ingestion.reference_provenance import resolve_security, upsert_reference_source

if __package__:
    from scripts.backfill_derived_security_metrics import _facts, _market
else:
    from backfill_derived_security_metrics import _facts, _market

END = date(2026, 3, 31)
DIRECTORY = Path(__file__).parent / "official_inputs" / "insurer-shares-20260331"
HDFC_URL = ("https://www.hdfclife.com/content/dam/hdfclifeinsurancecompany/about-us/pdf/"
            "investor-relations/financial-information/annual-reports/Integrated-Annual-Report-FY-2025-26.pdf")
HDFC_PDF_SHA = "b7a552082ee124037a5075412e3680b21ca6ede0c1d4c2c1c6a032afc0490e9c"
HDFC_PAGE_SHA = "6c02f3df8975d46dda449b9ff621c6f66dc6c5ed42dc34a65f8b6b905ebedcd0"
SBI_URL = "https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_LI_1657299_22042026060213_WEB.xml"
SBI_SHA = "1ec804878eccf10bd21d54602bc79bf5a3d58d566d421659634549947c406221"
TARGETS = ("AXISBANK", "BAJAJFINSV", "BAJFINANCE", "HDFCBANK", "HDFCLIFE", "ICICIBANK",
           "JIOFIN", "KOTAKBANK", "SBILIFE", "SBIN", "SHRIRAMFIN")


def reviewed_shares() -> dict[str, RawFinancialFact]:
    page = (DIRECTORY / "hdfclife-page166.txt").read_bytes()
    xml = (DIRECTORY / "sbilife-original.xml").read_bytes()
    if hashlib.sha256(page).hexdigest() != HDFC_PAGE_SHA or hashlib.sha256(xml).hexdigest() != SBI_SHA:
        raise ValueError("Reviewed insurer input checksum mismatch")
    content = " ".join(page.decode().split())
    if ("March 31, 2026" not in content or "13. Share Capital and Debentures" not in content
            or "21,57,81,95,360 comprising 2,15,78,19,536 equity" not in content
            or "shares having face value of ` 10/- each." not in content):
        raise ValueError("Reviewed HDFC share-capital paragraph mismatch")
    match = re.search(r"([\d,]+) comprising ([\d,]+) equity", content)
    if match is None:
        raise ValueError("Missing reported HDFC share count")
    capital, hdfc_shares = (Decimal(value.replace(",", "")) for value in match.groups())
    if hdfc_shares * 10 != capital:
        raise ValueError("HDFC reported capital and shares do not reconcile")
    root = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
    nodes = list(root.iter())

    def element(name: str, context: str) -> etree._Element:
        matches = [node for node in nodes if isinstance(node.tag, str)
                   and etree.QName(node).localname == name and node.get("contextRef") == context]
        if len(matches) != 1:
            raise ValueError("Ambiguous SBI reported element")
        return matches[0]

    if element("Symbol", "OneD").text != "SBILIFE" or element("ISIN", "OneD").text != "INE123W01016":
        raise ValueError("SBI official issuer identity mismatch")
    contexts = {node.get("id"): node for node in nodes
                if isinstance(node.tag, str) and etree.QName(node).localname == "context"}
    for context, end_element in (("OneI", "instant"), ("OneD", "endDate")):
        dates = contexts[context].xpath(f".//*[local-name()='{end_element}']/text()")
        if dates != [END.isoformat()]:
            raise ValueError("SBI share input period mismatch")
    notes = element("DisclosureOfNotesOnFinancialResultsExplanatoryTextBlock", "OneD").text or ""
    face = re.findall(r"equity shares with face value of Rs\.\s*(\d+(?:\.\d+)?) each", notes)
    if not face or len(set(face)) != 1:
        raise ValueError("Missing or ambiguous SBI equity face value")
    capital_node = element("PaidUpEquityShareCapital", "OneI")
    if capital_node.get("unitRef") != "INR":
        raise ValueError("SBI capital currency mismatch")
    sbi_shares = Decimal(capital_node.text or "0") / Decimal(face[0])
    if sbi_shares <= 0 or sbi_shares != sbi_shares.to_integral_value():
        raise ValueError("SBI paid-up capital and face value do not reconcile")
    return {
        "HDFCLIFE": RawFinancialFact(
            name="shares_outstanding", period_start=None, period_end=END, period_type="point_in_time",
            value=hdfc_shares, unit="shares",
            metadata={"source_format": "reviewed_issuer_pdf_excerpt", "page_number": 166,
                      "document_sha256": HDFC_PDF_SHA, "excerpt_sha256": HDFC_PAGE_SHA,
                      "reported_paid_up_capital": str(capital), "share_count_basis": "reported_ordinary_shares"}),
        "SBILIFE": RawFinancialFact(
            name="shares_outstanding", period_start=None, period_end=END, period_type="point_in_time",
            value=sbi_shares, unit="shares",
            metadata={"source_format": "reviewed_official_xbrl", "document_sha256": SBI_SHA,
                      "derived": True, "formula": "reported_paid_up_equity_capital / filed_equity_face_value",
                      "reported_capital_decimals": capital_node.get("decimals"),
                      "reported_face_value": face[0], "xbrl_context_id": "OneI",
                      "face_value_notes_context": "OneD", "share_count_basis": "paid_up_equity_capital"}),
    }


async def complete(dry_run: bool, approval_reference: str) -> None:
    shares = reviewed_shares()
    print(json.dumps({"event": "validated_insurer_shares", "dry_run": dry_run,
                      "shares": {symbol: str(fact.value) for symbol, fact in shares.items()}}), flush=True)
    if dry_run:
        return
    settings = get_settings()
    if not settings.database_url:
        raise ValueError("Existing database configuration is required")
    engine = create_database_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            size = await connection.scalar(text("select pg_database_size(current_database())"))
            identities = (await connection.execute(text("""
                select nse_symbol,isin from securities where id in
                  (select distinct security_id from security_agent_readiness_status)
                  and nse_symbol in ('HDFCLIFE','SBILIFE')
            """))).mappings().all()
        if (size is None or size >= 450_000_000 or {row["nse_symbol"]: row["isin"] for row in identities}
                != {"HDFCLIFE": "INE795G01014", "SBILIFE": "INE123W01016"}):
            raise ValueError("Prepared issuer or free-tier storage guard failed")
        for symbol, fact in shares.items():
            security_id, _ = await resolve_security(engine, symbol)
            source_id = await upsert_reference_source(
                engine, security_id=security_id, source_type="reference_financials",
                source_uri=HDFC_URL if symbol == "HDFCLIFE" else SBI_URL,
                title=f"{symbol} reviewed ordinary share inputs at 31 March 2026",
                published_at=None, checksum=HDFC_PDF_SHA if symbol == "HDFCLIFE" else SBI_SHA,
                approval_reference=approval_reference if symbol == "HDFCLIFE" else None,
                metadata={"importer": "complete_remaining_peer_metrics", "verified_isin":
                          "INE795G01014" if symbol == "HDFCLIFE" else "INE123W01016",
                          "original_retrieval_date": "2026-10-01", **fact.metadata},
            )
            await FinancialFactIngestor(engine).ingest_batch(
                security_id=security_id, source_id=source_id, facts=[fact])
        ingestor = DerivedSecurityMetricIngestor(engine)
        for symbol in TARGETS:
            security_id, _ = await resolve_security(engine, symbol)
            facts, market = await _facts(engine, security_id), await _market(engine, security_id)
            async with engine.connect() as connection:
                approved = set((await connection.execute(text("""
                    select id from sources where security_id=:id
                      and metadata->>'production_approved'='true'
                """), {"id": security_id})).scalars())
            # Restricted legacy facts must not win concept selection over filed inputs.
            facts = [fact for fact in facts if fact.source_id in approved]
            if market is None or market.source_id not in approved:
                raise ValueError(f"Approved EOD price is required for {symbol}")
            bundle = derive_peer_metrics(facts, market=market)
            for partition in partition_metric_bundle(bundle):
                await ingestor.ingest(security_id=security_id, symbol=symbol, bundle=partition)
            readiness = await refresh_prepared_security_readiness(engine, security_id, settings)
            print(json.dumps({"event": "remaining_peer_completed", "symbol": symbol,
                              "comparable_count": bundle.industry_comparable_count,
                              "readiness": readiness.as_dict()}, default=str), flush=True)
        print(json.dumps({"status": "completed", "securities": len(TARGETS),
                          "new_share_facts": len(shares)}), flush=True)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--approval-reference", required=True)
    args = parser.parse_args()
    asyncio.run(complete(args.dry_run, args.approval_reference))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
