from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class RawFinancialFact:
    name: str
    period_end: date
    value: int | float | Decimal | str
    period_type: str
    unit: str | None = None
    period_start: date | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedFinancialFact:
    fact_name: str
    period_end: date
    value: Decimal
    period_type: str
    unit: str | None = None
    period_start: date | None = None
    metadata: dict[str, object] = field(default_factory=dict)


_FACT_ALIASES = {
    "revenue": "revenue",
    "revenue from operations": "revenue",
    "total revenue": "revenue",
    "sales": "revenue",
    "net sales": "revenue",
    "ebitda": "ebitda",
    "operating ebitda": "ebitda",
    "ebit": "ebit",
    "operating profit": "ebit",
    "profit before interest and tax": "ebit",
    "pat": "pat",
    "profit after tax": "pat",
    "net profit": "pat",
    "profit for the period": "pat",
    "cash flow from operating activities": "cfo",
    "net cash from operating activities": "cfo",
    "operating cash flow": "cfo",
    "cfo": "cfo",
    "capital expenditure": "capex",
    "capex": "capex",
    "free cash flow": "free_cash_flow",
    "fcf": "free_cash_flow",
    "total debt": "total_debt",
    "borrowings": "total_debt",
    "cash and cash equivalents": "cash",
    "cash equivalents": "cash",
    "cash": "cash",
    "trade receivables": "receivables",
    "receivables": "receivables",
    "inventories": "inventory",
    "inventory": "inventory",
    "trade payables": "payables",
    "payables": "payables",
    "total assets": "total_assets",
    "current liabilities": "current_liabilities",
    "interest expense": "interest_expense",
    "finance costs": "interest_expense",
    "shares outstanding": "shares_outstanding",
    "number of shares": "shares_outstanding",
    "book value per share": "book_value_per_share",
    "sales per share": "sales_per_share",
    "gross npa": "gross_npa_pct",
    "gross npa pct": "gross_npa_pct",
    "gross npa percentage": "gross_npa_pct",
    "net npa": "net_npa_pct",
    "net npa pct": "net_npa_pct",
    "net interest margin": "nim_pct",
    "nim": "nim_pct",
    "casa ratio": "casa_ratio_pct",
    "credit cost": "credit_cost_pct",
    "capital adequacy ratio": "capital_adequacy_pct",
    "crar": "capital_adequacy_pct",
    "return on assets": "roa_pct",
    "return on equity": "roe_pct",
    "aum": "aum",
    "assets under management": "aum",
    "disbursements": "disbursements",
    "annual premium equivalent": "ape",
    "ape": "ape",
    "value of new business": "vnb",
    "vnb": "vnb",
    "vnb margin": "vnb_margin_pct",
    "embedded value": "embedded_value",
    "embedded value per share": "embedded_value_per_share",
    "solvency ratio": "solvency_ratio_pct",
    "attrition": "attrition_pct",
    "utilization": "utilization_pct",
    "total contract value": "tcv",
    "tcv": "tcv",
    "constant currency growth": "constant_currency_growth_pct",
    "volume growth": "volume_growth_pct",
    "market share": "market_share_pct",
}

_PERIOD_TYPES = {
    "annual": "annual",
    "year": "annual",
    "yearly": "annual",
    "fy": "annual",
    "quarter": "quarterly",
    "quarterly": "quarterly",
    "q": "quarterly",
    "half year": "half_year",
    "half-year": "half_year",
    "half_year": "half_year",
    "ttm": "ttm",
    "trailing twelve months": "ttm",
    "point in time": "point_in_time",
    "point_in_time": "point_in_time",
}


class FinancialFactIngestor:
    """Normalizes sourced financial facts into the canonical research schema."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def ingest_batch(
        self,
        *,
        security_id: UUID,
        source_id: UUID,
        facts: list[RawFinancialFact],
    ) -> dict[str, int]:
        normalized = normalize_financial_facts(facts)
        async with self.engine.begin() as connection:
            for fact in normalized:
                await _upsert_financial_fact(
                    connection,
                    security_id=security_id,
                    source_id=source_id,
                    fact=fact,
                )
        return {
            "input_count": len(facts),
            "normalized_count": len(normalized),
            "derived_count": sum(bool(fact.metadata.get("derived")) for fact in normalized),
        }


def normalize_financial_facts(facts: list[RawFinancialFact]) -> list[NormalizedFinancialFact]:
    normalized: dict[tuple[str, date, str], NormalizedFinancialFact] = {}
    for fact in facts:
        item = normalize_financial_fact(fact)
        normalized[(item.fact_name, item.period_end, item.period_type)] = item

    grouped: dict[tuple[date, str], dict[str, NormalizedFinancialFact]] = {}
    for item in normalized.values():
        grouped.setdefault((item.period_end, item.period_type), {})[item.fact_name] = item

    for (period_end, period_type), period in grouped.items():
        if "free_cash_flow" in period or "cfo" not in period or "capex" not in period:
            continue
        cfo = period["cfo"]
        capex = period["capex"]
        derived = NormalizedFinancialFact(
            fact_name="free_cash_flow",
            period_start=cfo.period_start,
            period_end=period_end,
            period_type=period_type,
            value=cfo.value - abs(capex.value),
            unit=cfo.unit or capex.unit,
            metadata={
                "derived": True,
                "formula": "cfo - abs(capex)",
                "components": ["cfo", "capex"],
            },
        )
        normalized[(derived.fact_name, period_end, period_type)] = derived

    return sorted(
        normalized.values(),
        key=lambda item: (item.period_end, item.period_type, item.fact_name),
    )


def normalize_financial_fact(fact: RawFinancialFact) -> NormalizedFinancialFact:
    if not fact.name.strip():
        raise ValueError("financial fact name cannot be empty")
    canonical = canonical_fact_name(fact.name)
    period_type = canonical_period_type(fact.period_type)
    value = _decimal(fact.value)
    if canonical == "shares_outstanding" and value <= 0:
        raise ValueError("shares_outstanding must be positive")

    return NormalizedFinancialFact(
        fact_name=canonical,
        period_start=fact.period_start,
        period_end=fact.period_end,
        period_type=period_type,
        value=value,
        unit=fact.unit.strip() if fact.unit else None,
        metadata=dict(fact.metadata),
    )


def canonical_fact_name(value: str) -> str:
    normalized = _normalize_label(value)
    return _FACT_ALIASES.get(normalized, normalized.replace(" ", "_"))


def canonical_period_type(value: str) -> str:
    normalized = _normalize_label(value)
    return _PERIOD_TYPES.get(normalized, normalized.replace(" ", "_"))


async def _upsert_financial_fact(
    connection: object,
    *,
    security_id: UUID,
    source_id: UUID,
    fact: NormalizedFinancialFact,
) -> None:
    parameters = {
        "security_id": security_id,
        "source_id": source_id,
        "fact_name": fact.fact_name,
        "period_start": fact.period_start,
        "period_end": fact.period_end,
        "period_type": fact.period_type,
        "value": fact.value,
        "unit": fact.unit,
        "data": json.dumps(fact.metadata),
    }
    result = await connection.execute(
        text(
            """
            insert into financial_facts (
                security_id, fact_name, period_start, period_end, period_type,
                value, unit, source_id, data
            ) values (
                :security_id, :fact_name, :period_start, :period_end, :period_type,
                :value, :unit, :source_id, cast(:data as jsonb)
            )
            on conflict do nothing
            returning id
            """
        ),
        parameters,
    )
    inserted_id = result.scalar_one_or_none()
    if inserted_id is not None:
        return

    await connection.execute(
        text(
            """
            update financial_facts
            set period_start = :period_start,
                value = :value,
                unit = :unit,
                data = cast(:data as jsonb)
            where security_id = :security_id
              and fact_name = :fact_name
              and period_end = :period_end
              and period_type = :period_type
              and source_id is not distinct from :source_id
            """
        ),
        parameters,
    )


def _normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def _decimal(value: int | float | Decimal | str) -> Decimal:
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"invalid numeric financial fact: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("financial fact must be finite")
    return result
