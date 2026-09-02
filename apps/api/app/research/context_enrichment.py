from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.agents.contracts import EvidenceRef

Row = Mapping[str, Any]
PEER_METRICS = ("revenue_growth", "ebitda_margin", "roce", "pe", "pb", "ev_ebitda")


async def load_context_enrichment(
    connection: AsyncConnection,
    *,
    security_id: UUID,
    security: Row,
    financials: dict[str, object],
) -> tuple[dict[str, object], list[EvidenceRef]]:
    macro_rows = await _load_macro_rows(connection)
    metric_rows = await _load_security_metric_rows(connection, security_id)
    peer_rows = await _load_peer_rows(connection, security_id, security)
    benchmark_rows = await _load_benchmark_rows(connection, security_id, security)

    macro = build_macro_context(macro_rows)
    benchmarks = build_benchmark_context(benchmark_rows)
    if "india_vix" not in macro and benchmarks.get("india_vix") is not None:
        macro["india_vix"] = benchmarks["india_vix"]

    company_metrics = build_company_metrics(financials, metric_rows)
    peers = build_peer_context(peer_rows)

    context: dict[str, object] = {
        "company_metrics": company_metrics,
        "peers": peers,
    }
    if macro:
        context["macro"] = macro
    if benchmarks.get("benchmark"):
        context["benchmark"] = benchmarks["benchmark"]
    if benchmarks.get("sector_benchmark"):
        context["sector_benchmark"] = benchmarks["sector_benchmark"]

    evidence = [
        *_macro_evidence(macro_rows),
        *_metric_evidence(metric_rows, security_id),
        *_peer_evidence(peer_rows),
        *_benchmark_evidence(benchmark_rows),
    ]
    return context, evidence


async def _load_macro_rows(connection: AsyncConnection) -> list[Row]:
    result = await connection.execute(
        text(
            """
            with ranked as (
              select mo.series_key, mo.observation_date, mo.value, mo.unit, mo.source_id,
                     mo.released_at, mo.metadata,
                     row_number() over (
                       partition by mo.series_key
                       order by mo.observation_date desc, mo.created_at desc
                     ) as rn
              from macro_observations mo
              where mo.series_key in (
                'repo_rate', 'india_10y_yield', 'usd_inr', 'brent', 'india_vix',
                'cpi_yoy', 'iip_yoy', 'fii_cash_net_cr', 'dii_cash_net_cr'
              )
            )
            select r.series_key, r.observation_date, r.value, r.unit, r.source_id,
                   r.released_at, r.metadata, r.rn,
                   s.source_type, s.source_uri, s.title, s.published_at,
                   s.retrieved_at, s.freshness, s.checksum
            from ranked r
            left join sources s on s.id = r.source_id
            where r.rn <= 2
            order by r.series_key, r.rn
            """
        )
    )
    return list(result.mappings().all())


async def _load_security_metric_rows(
    connection: AsyncConnection,
    security_id: UUID,
) -> list[Row]:
    result = await connection.execute(
        text(
            """
            with ranked as (
              select sm.metric_name, sm.as_of_date, sm.value, sm.unit, sm.source_id,
                     sm.metadata,
                     row_number() over (
                       partition by sm.metric_name
                       order by sm.as_of_date desc, sm.created_at desc
                     ) as rn
              from security_metrics sm
              where sm.security_id = :security_id
                and sm.metric_name in (
                  'revenue_growth', 'ebitda_margin', 'roce', 'pe', 'pb', 'ev_ebitda'
                )
            )
            select r.metric_name, r.as_of_date, r.value, r.unit, r.source_id, r.metadata,
                   s.source_type, s.source_uri, s.title, s.published_at,
                   s.retrieved_at, s.freshness, s.checksum
            from ranked r
            left join sources s on s.id = r.source_id
            where r.rn = 1
            order by r.metric_name
            """
        ),
        {"security_id": security_id},
    )
    return list(result.mappings().all())


async def _load_peer_rows(
    connection: AsyncConnection,
    security_id: UUID,
    security: Row,
) -> list[Row]:
    result = await connection.execute(
        text(
            """
            with peer_candidates as (
              select s.id, s.legal_name, s.nse_symbol, s.sector, s.industry,
                     case
                       when :industry is not null and s.industry = :industry then 0
                       when :sector is not null and s.sector = :sector then 1
                       else 2
                     end as match_rank
              from securities s
              where s.id <> :security_id
                and (
                  (:industry is not null and s.industry = :industry)
                  or (:sector is not null and s.sector = :sector)
                )
              order by match_rank, s.legal_name
              limit 8
            ), latest_metrics as (
              select distinct on (sm.security_id, sm.metric_name)
                     sm.security_id, sm.metric_name, sm.value, sm.unit,
                     sm.as_of_date, sm.source_id
              from security_metrics sm
              join peer_candidates pc on pc.id = sm.security_id
              where sm.metric_name in (
                'revenue_growth', 'ebitda_margin', 'roce', 'pe', 'pb', 'ev_ebitda'
              )
              order by sm.security_id, sm.metric_name, sm.as_of_date desc, sm.created_at desc
            )
            select pc.id, pc.legal_name, pc.nse_symbol, pc.sector, pc.industry,
                   lm.metric_name, lm.value, lm.unit, lm.as_of_date, lm.source_id,
                   s.source_type, s.source_uri, s.title, s.published_at,
                   s.retrieved_at, s.freshness, s.checksum
            from peer_candidates pc
            left join latest_metrics lm on lm.security_id = pc.id
            left join sources s on s.id = lm.source_id
            order by pc.match_rank, pc.legal_name, lm.metric_name
            """
        ),
        {
            "security_id": security_id,
            "sector": security.get("sector"),
            "industry": security.get("industry"),
        },
    )
    return list(result.mappings().all())


async def _load_benchmark_rows(
    connection: AsyncConnection,
    security_id: UUID,
    security: Row,
) -> list[Row]:
    fallback_sector_code = sector_benchmark_code(
        str(security.get("sector") or ""),
        str(security.get("industry") or ""),
    )
    result = await connection.execute(
        text(
            """
            with requested as (
              select 'market'::text as role, b.id, b.code, b.name
              from benchmarks b where b.code = 'NIFTY50'
              union all
              select 'sector'::text as role, b.id, b.code, b.name
              from security_benchmarks sb
              join benchmarks b on b.id = sb.benchmark_id
              where sb.security_id = :security_id and sb.role = 'sector'
              union all
              select 'sector'::text as role, b.id, b.code, b.name
              from benchmarks b
              where b.code = :fallback_sector_code
                and not exists (
                  select 1 from security_benchmarks
                  where security_id = :security_id and role = 'sector'
                )
              union all
              select 'volatility'::text as role, b.id, b.code, b.name
              from benchmarks b where b.code = 'INDIAVIX'
            ), ranked as (
              select r.role, r.code, r.name, bb.ts, bb.close, bb.provider,
                     row_number() over (
                       partition by r.role, r.code
                       order by bb.ts desc
                     ) as rn
              from requested r
              left join benchmark_bars bb
                on bb.benchmark_id = r.id and bb.interval in ('1d', 'day', 'daily')
            )
            select role, code, name, ts, close, provider, rn
            from ranked
            where rn <= 2
            order by role, code, rn
            """
        ),
        {"security_id": security_id, "fallback_sector_code": fallback_sector_code},
    )
    return list(result.mappings().all())


def build_macro_context(rows: list[Row]) -> dict[str, object]:
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["series_key"])].append(row)

    macro: dict[str, object] = {}
    for series_key, observations in grouped.items():
        observations.sort(key=lambda item: int(item["rn"]))
        latest = _number(observations[0].get("value"))
        if latest is not None:
            macro[series_key] = latest
        if series_key not in {"usd_inr", "brent"} or len(observations) < 2:
            continue
        previous = _number(observations[1].get("value"))
        change = percent_change(latest, previous)
        if change is not None:
            macro[f"{series_key}_change_pct"] = change
    return macro


def build_company_metrics(
    financials: dict[str, object],
    metric_rows: list[Row],
) -> dict[str, object]:
    metrics = {
        str(row["metric_name"]): _number(row.get("value"))
        for row in metric_rows
        if row.get("value") is not None
    }
    current_revenue = _number(financials.get("revenue"))
    previous_revenue = _number(financials.get("previous_revenue"))
    ebitda = _number(financials.get("ebitda"))
    ebit = _number(financials.get("ebit"))
    total_assets = _number(financials.get("total_assets"))
    current_liabilities = _number(financials.get("current_liabilities"))

    metrics.setdefault("revenue_growth", ratio_change(current_revenue, previous_revenue))
    metrics.setdefault("ebitda_margin", safe_ratio(ebitda, current_revenue))
    capital_employed = None
    if total_assets is not None and current_liabilities is not None:
        capital_employed = total_assets - current_liabilities
    metrics.setdefault("roce", safe_ratio(ebit, capital_employed))
    return {key: value for key, value in metrics.items() if value is not None}


def build_peer_context(rows: list[Row]) -> list[dict[str, object]]:
    peers: dict[str, dict[str, object]] = {}
    for row in rows:
        peer_id = str(row["id"])
        peer = peers.setdefault(
            peer_id,
            {
                "security_id": peer_id,
                "legal_name": row["legal_name"],
                "nse_symbol": row["nse_symbol"],
                "sector": row["sector"],
                "industry": row["industry"],
            },
        )
        metric_name = row.get("metric_name")
        if metric_name and row.get("value") is not None:
            peer[str(metric_name)] = float(row["value"])
    return [peer for peer in peers.values() if any(metric in peer for metric in PEER_METRICS)]


def build_benchmark_context(rows: list[Row]) -> dict[str, object]:
    grouped: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for row in rows:
        if row.get("ts") is None or row.get("close") is None:
            continue
        grouped[(str(row["role"]), str(row["code"]))].append(row)

    output: dict[str, object] = {}
    for (role, _code), observations in grouped.items():
        observations.sort(key=lambda item: int(item["rn"]))
        latest = _number(observations[0].get("close"))
        previous = _number(observations[1].get("close")) if len(observations) > 1 else None
        if role == "volatility":
            output["india_vix"] = latest
            continue
        payload = {
            "name": observations[0]["name"],
            "change_pct": percent_change(latest, previous),
            "as_of": observations[0]["ts"].isoformat(),
            "provider": observations[0]["provider"],
        }
        if role == "market":
            output["benchmark"] = payload
        elif role == "sector":
            output["sector_benchmark"] = payload
    return output


def sector_benchmark_code(sector: str, industry: str) -> str | None:
    text_value = f"{sector} {industry}".lower()
    if any(term in text_value for term in ("bank", "banking")):
        return "NIFTYBANK"
    if any(term in text_value for term in ("information technology", "it services", "software")):
        return "NIFTYIT"
    return None


def percent_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0}:
        return None
    return (current / previous - 1.0) * 100.0


def ratio_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0}:
        return None
    return current / previous - 1.0


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def _macro_evidence(rows: list[Row]) -> list[EvidenceRef]:
    return _rows_to_evidence(rows, category="macro")


def _metric_evidence(rows: list[Row], security_id: UUID) -> list[EvidenceRef]:
    evidence = _rows_to_evidence(rows, category="security_metric")
    if evidence or not rows:
        return evidence
    return [
        EvidenceRef(
            source_type="security_metric",
            source_uri=f"db://security-metrics/{security_id}",
            title="Normalized security metrics",
            retrieved_at=datetime.now(UTC).isoformat(),
            freshness="periodic",
            source_priority=2,
        )
    ]


def _peer_evidence(rows: list[Row]) -> list[EvidenceRef]:
    return _rows_to_evidence(rows, category="peer_metric")


def _benchmark_evidence(rows: list[Row]) -> list[EvidenceRef]:
    latest_by_code: dict[str, Row] = {}
    for row in rows:
        if row.get("ts") is None:
            continue
        latest_by_code.setdefault(str(row["code"]), row)
    now = datetime.now(UTC).isoformat()
    return [
        EvidenceRef(
            source_type="market_data",
            source_uri=f"db://benchmark-bars/{row['code']}",
            title=str(row["name"]),
            published_at=row["ts"].isoformat(),
            retrieved_at=now,
            freshness="historical",
            excerpt=f"Normalized benchmark close for {row['code']} from {row['provider']}.",
            source_priority=2,
        )
        for row in latest_by_code.values()
    ]


def _rows_to_evidence(rows: list[Row], *, category: str) -> list[EvidenceRef]:
    by_source: dict[str, Row] = {}
    for row in rows:
        source_id = row.get("source_id")
        if source_id is None:
            continue
        by_source.setdefault(str(source_id), row)

    now = datetime.now(UTC).isoformat()
    evidence: list[EvidenceRef] = []
    for row in by_source.values():
        source_type = str(row.get("source_type") or category)
        source_uri = str(row.get("source_uri") or f"db://{category}/{row['source_id']}")
        evidence.append(
            EvidenceRef(
                source_type=source_type,
                source_uri=source_uri,
                title=row.get("title") or category.replace("_", " ").title(),
                published_at=_iso(row.get("published_at")) or _iso(row.get("released_at")),
                retrieved_at=_iso(row.get("retrieved_at")) or now,
                freshness=_freshness(row.get("freshness"), fallback="periodic"),
                checksum=row.get("checksum"),
                source_priority=1 if source_type in {"official_macro", "official_flow"} else 2,
            )
        )
    return evidence


def _freshness(value: object, *, fallback: str) -> str:
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


def _number(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
