from datetime import date
from decimal import Decimal

import pytest

from app.ingestion.financials import RawFinancialFact
from app.ingestion.xbrl_evidence import build_xbrl_evidence_chunks


def _fact(
    name: str,
    value: str,
    *,
    period_end: date = date(2026, 6, 30),
    period_type: str = "quarterly",
    unit: str | None = "INR",
    element: str | None = None,
) -> RawFinancialFact:
    return RawFinancialFact(
        name=name,
        period_end=period_end,
        value=Decimal(value),
        period_type=period_type,
        unit=unit,
        metadata={"xbrl_element": element} if element else {},
    )


def test_build_xbrl_evidence_is_deterministic_and_non_ai() -> None:
    facts = [
        _fact("Profit After Tax", "125.50", element="ProfitLossForPeriod"),
        _fact("Revenue", "1000", element="RevenueFromOperations"),
    ]

    chunks = build_xbrl_evidence_chunks(facts)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_index == 0
    assert chunk.period_end == date(2026, 6, 30)
    assert chunk.period_type == "quarterly"
    assert chunk.metadata["ai_assisted"] is False
    assert chunk.metadata["evidence_kind"] == "deterministic_xbrl_fact_summary"
    assert chunk.metadata["fact_count"] == 2
    assert "Profit After Tax: 125.50 INR" in chunk.content
    assert "Revenue: 1000 INR" in chunk.content
    assert "xbrl_element=ProfitLossForPeriod" in chunk.content
    assert chunk.content.index("Profit After Tax") < chunk.content.index("Revenue")


def test_build_xbrl_evidence_groups_periods_and_chunks_large_periods() -> None:
    facts = [
        _fact("Revenue", "1000"),
        _fact("Profit After Tax", "125"),
        _fact("Revenue", "900", period_end=date(2026, 3, 31)),
    ]

    chunks = build_xbrl_evidence_chunks(facts, max_facts_per_chunk=1)

    assert len(chunks) == 3
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]
    assert [chunk.period_end for chunk in chunks] == [
        date(2026, 6, 30),
        date(2026, 6, 30),
        date(2026, 3, 31),
    ]
    assert all(chunk.metadata["fact_count"] == 1 for chunk in chunks)


def test_build_xbrl_evidence_preserves_unspecified_units_without_inference() -> None:
    chunks = build_xbrl_evidence_chunks(
        [_fact("Employee Count", "1234", unit=None)]
    )

    assert "Employee Count: 1234 unit_unspecified" in chunks[0].content


def test_build_xbrl_evidence_rejects_invalid_chunk_size_and_fact_name() -> None:
    with pytest.raises(ValueError, match="max_facts_per_chunk"):
        build_xbrl_evidence_chunks([_fact("Revenue", "1")], max_facts_per_chunk=0)

    with pytest.raises(ValueError, match="empty fact name"):
        build_xbrl_evidence_chunks([_fact("   ", "1")])


def test_build_xbrl_evidence_empty_input_is_empty_not_fabricated() -> None:
    assert build_xbrl_evidence_chunks([]) == ()
