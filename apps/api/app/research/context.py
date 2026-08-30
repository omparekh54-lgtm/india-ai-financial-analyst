from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from statistics import mean
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import EvidenceRef
from app.research.context_enrichment import load_context_enrichment

Row = Mapping[str, Any]


class DatabaseResearchContextLoader:
    """Hydrates the agent DAG from normalized, provenance-aware database state."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def load(
        self,
        security_id: UUID,
        *,
        mode: str,
    ) -> tuple[dict[str, object], list[EvidenceRef]]:
        async with self.engine.connect() as connection:
            security = (
                await connection.execute(
                    text(
                        """
                        select id, legal_name, nse_symbol, bse_code, isin, sector, industry,
                               primary_exchange
                        from securities where id = :security_id
                        """
                    ),
                    {"security_id": security_id},
                )
            ).mappings().one()

            financial_rows = (
                await connection.execute(
                    text(
                        """
                        with ranked as (
                          select ff.fact_name, ff.value, ff.unit, ff.period_end, ff.period_type,
                                 ff.source_id,
                                 row_number() over (
                                   partition by ff.fact_name
                                   order by ff.period_end desc, ff.created_at desc
                                 ) as rn
                          from financial_facts ff
                          where ff.security_id = :security_id
                        )
                        select r.fact_name, r.value, r.unit, r.period_end, r.period_type,
                               r.source_id, r.rn,
                               s.source_type, s.source_uri, s.title, s.published_at,
                               s.retrieved_at, s.freshness, s.checksum
                        from ranked r
                        left join sources s on s.id = r.source_id
                        where r.rn <= 2
                        order by r.fact_name, r.rn
                        """
                    ),
                    {"security_id": security_id},
                )
            ).mappings().all()

            bar_rows = (
                await connection.execute(
                    text(
                        """
                        select ts, open, high, low, close, volume, provider, is_adjusted
                        from market_bars
                        where security_id = :security_id and interval in ('1d', 'day', 'daily')
                        order by ts desc
                        limit 160
                        """
                    ),
                    {"security_id": security_id},
                )
            ).mappings().all()

            event_rows = (
                await connection.execute(
                    text(
                        """
                        select ce.id, ce.event_type, ce.headline, ce.event_at, ce.materiality, ce.data,
                               s.source_type, s.source_uri, s.title, s.published_at, s.retrieved_at,
                               s.freshness, s.checksum
                        from corporate_events ce
                        left join sources s on s.id = ce.source_id
                        where ce.security_id = :security_id
                        order by ce.event_at desc nulls last, ce.created_at desc
                        limit 100
                        """
                    ),
                    {"security_id": security_id},
                )
            ).mappings().all()

            snapshot = None
            if mode == "what_changed":
                snapshot = (
                    await connection.execute(
                        text(
                            """
                            select snapshot_at, metrics, catalysts, risks, metadata
                            from analysis_snapshots
                            where security_id = :security_id
                            order by snapshot_at desc
                            limit 1
                            """
                        ),
                        {"security_id": security_id},
                    )
                ).mappings().first()

            financials = _financial_context(financial_rows)
            enrichment_context, enrichment_evidence = await load_context_enrichment(
                connection,
                security_id=security_id,
                security=security,
                financials=financials,
            )

        bars = _market_bars(bar_rows)
        context: dict[str, object] = {
            "security": {key: str(value) if key == "id" else value for key, value in security.items()},
            "financials": financials,
            "market_bars": bars,
            "governance": _governance_context(event_rows),
            "news_events": _event_context(event_rows),
            "narratives": [row["headline"] for row in event_rows if row["headline"]],
        }
        context.update(enrichment_context)

        quote = _market_quote(bar_rows)
        if quote:
            context["market_quote"] = quote

        valuation_inputs = _valuation_factual_inputs(security, financials, quote)
        if valuation_inputs:
            context["valuation_inputs"] = valuation_inputs

        if snapshot is not None:
            context["previous_snapshot"] = {
                "snapshot_at": snapshot["snapshot_at"].isoformat(),
                "metrics": snapshot["metrics"],
                "catalysts": snapshot["catalysts"],
                "risks": snapshot["risks"],
                "metadata": snapshot["metadata"],
            }

        evidence = [
            *_financial_evidence(financial_rows, security_id),
            *_market_evidence(bar_rows, security_id),
            *_event_evidence(event_rows),
            *enrichment_evidence,
        ]
        return context, _dedupe_evidence(evidence)


def _financial_context(rows: list[Row]) -> dict[str, object]:
    facts: dict[str, object] = {}
    for row in rows:
        name = str(row["fact_name"])
        value = float(row["value"]) if row["value"] is not None else None
        if row["rn"] == 1:
            facts[name] = value
            facts[f"{name}_period_end"] = row["period_end"].isoformat()
        elif row["rn"] == 2:
            facts[f"previous_{name}"] = value
    return facts


def _financial_evidence(rows: list[Row], security_id: UUID) -> list[EvidenceRef]:
    grouped: dict[str, Row] = {}
    for row in rows:
        key = str(row.get("source_id") or f"db:{row['fact_name']}:{row['period_end']}")
        grouped.setdefault(key, row)

    evidence: list[EvidenceRef] = []
    now = datetime.now(UTC).isoformat()
    for row in grouped.values():
        source_type = str(row.get("source_type") or "financial_fact")
        source_uri = str(
            row.get("source_uri")
            or f"db://financial-facts/{security_id}/{row['fact_name']}/{row['period_end']}"
        )
        freshness = _freshness(row.get("freshness"), fallback="periodic")
        evidence.append(
            EvidenceRef(
                source_type=source_type,
                source_uri=source_uri,
                title=row.get("title") or f"Financial facts through {row['period_end']}",
                published_at=_iso(row.get("published_at")),
                retrieved_at=_iso(row.get("retrieved_at")) or now,
                freshness=freshness,
                excerpt=(
                    f"Normalized {row['fact_name']}={row['value']} {row.get('unit') or ''} "
                    f"for {row['period_type']} ending {row['period_end']}."
                ),
                checksum=row.get("checksum"),
                source_priority=1 if source_type in {"exchange_filing", "company_filing"} else 2,
            )
        )
    return evidence


def _market_bars(rows: list[Row]) -> list[dict[str, object]]:
    return [
        {
            "ts": row["ts"].isoformat(),
            "open": _float(row["open"]),
            "high": _float(row["high"]),
            "low": _float(row["low"]),
            "close": _float(row["close"]),
            "volume": _float(row["volume"]),
            "provider": row["provider"],
            "is_adjusted": row["is_adjusted"],
        }
        for row in reversed(rows)
    ]


def _market_quote(rows: list[Row]) -> dict[str, object] | None:
    if not rows:
        return None
    latest = rows[0]
    previous = rows[1] if len(rows) > 1 else None
    recent_volumes = [_float(row["volume"]) for row in rows[:20]]
    clean_volumes = [value for value in recent_volumes if value is not None]
    return {
        "price": _float(latest["close"]),
        "previous_close": _float(previous["close"]) if previous else None,
        "volume": _float(latest["volume"]),
        "average_volume": mean(clean_volumes) if clean_volumes else None,
        "provider": latest["provider"],
        "is_delayed": True,
        "as_of": latest["ts"].isoformat(),
    }


def _market_evidence(rows: list[Row], security_id: UUID) -> list[EvidenceRef]:
    if not rows:
        return []
    latest = rows[0]
    providers = sorted({str(row["provider"]) for row in rows})
    return [
        EvidenceRef(
            source_type="market_data",
            source_uri=f"db://market-bars/{security_id}",
            title="Normalized market history",
            published_at=latest["ts"].isoformat(),
            retrieved_at=datetime.now(UTC).isoformat(),
            freshness="historical",
            excerpt=(
                f"{len(rows)} daily bars through {latest['ts'].isoformat()} from "
                f"providers: {', '.join(providers)}."
            ),
            source_priority=2,
        )
    ]


def _event_context(rows: list[Row]) -> list[dict[str, object]]:
    return [
        {
            "title": row["headline"],
            "url": row["source_uri"] or f"db://corporate-events/{row['id']}",
            "published_at": row["event_at"].isoformat() if row["event_at"] else None,
            "source": row["source_type"] or "normalized_event",
            "summary": row["headline"],
        }
        for row in rows
    ]


def _governance_context(rows: list[Row]) -> dict[str, object]:
    event_types = {str(row["event_type"]) for row in rows}
    governance: dict[str, object] = {
        "auditor_resignation_recent": "auditor_resignation" in event_types,
        "credit_rating_downgrade_recent": any(
            row["event_type"] == "credit_rating"
            and "downgrade" in str(row["headline"]).lower()
            for row in rows
        ),
    }
    for row in rows:
        if row["event_type"] != "promoter_pledge":
            continue
        data = row["data"] if isinstance(row["data"], dict) else {}
        pledge = data.get("promoter_pledge_pct")
        if pledge is not None:
            governance["promoter_pledge_pct"] = pledge
            break
    return governance


def _event_evidence(rows: list[Row]) -> list[EvidenceRef]:
    evidence: list[EvidenceRef] = []
    for row in rows:
        uri = row["source_uri"] or f"db://corporate-events/{row['id']}"
        source_type = row["source_type"] or "normalized_event"
        evidence.append(
            EvidenceRef(
                source_type=source_type,
                source_uri=uri,
                title=row["title"] or row["headline"],
                published_at=_iso(row["published_at"]) or _iso(row["event_at"]),
                retrieved_at=_iso(row["retrieved_at"]) or datetime.now(UTC).isoformat(),
                freshness=_freshness(row["freshness"]),
                excerpt=row["headline"],
                checksum=row["checksum"],
                source_priority=1 if source_type in {"exchange_filing", "regulator"} else 3,
            )
        )
    return evidence


def _valuation_factual_inputs(
    security: Row,
    financials: dict[str, object],
    quote: dict[str, object] | None,
) -> dict[str, object]:
    data: dict[str, object] = {"sector": security["sector"], "industry": security["industry"]}
    if quote and quote.get("price") is not None:
        data["current_price"] = quote["price"]
    for source, target in (
        ("free_cash_flow", "base_fcf"),
        ("shares_outstanding", "shares_outstanding"),
        ("book_value_per_share", "book_value_per_share"),
        ("embedded_value_per_share", "embedded_value_per_share"),
        ("sales_per_share", "sales_per_share"),
    ):
        if financials.get(source) is not None:
            data[target] = financials[source]
    debt = _number(financials.get("total_debt"))
    cash = _number(financials.get("cash"))
    if debt is not None and cash is not None:
        data["net_debt"] = debt - cash
    return data


def _freshness(value: object, *, fallback: str = "unknown") -> str:
    candidate = str(value or fallback)
    if candidate not in {"live", "near_live", "periodic", "historical", "unknown"}:
        return fallback
    return candidate


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _dedupe_evidence(items: list[EvidenceRef]) -> list[EvidenceRef]:
    seen: dict[tuple[str, str], EvidenceRef] = {}
    for item in items:
        seen.setdefault((item.source_type, item.source_uri), item)
    return list(seen.values())


def _float(value: object) -> float | None:
    return None if value is None else float(value)


def _number(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
