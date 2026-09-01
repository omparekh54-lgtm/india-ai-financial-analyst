from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ingestion.derived_metrics import PEER_USABLE_METRICS

MAX_COMPARE_SECURITIES = 5
MAX_SCREEN_FILTERS = 5
MAX_SCREEN_RESULTS = 100
_METRIC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class MetricFilter:
    metric_name: str
    min_value: float | None = None
    max_value: float | None = None

    def validate(self) -> None:
        if not _METRIC_NAME_RE.fullmatch(self.metric_name):
            raise ValueError("invalid metric_name")
        if self.metric_name not in PEER_USABLE_METRICS:
            raise ValueError(f"unsupported source-backed screening metric: {self.metric_name}")
        if self.min_value is None and self.max_value is None:
            raise ValueError("metric filter requires min_value or max_value")
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise ValueError("metric filter min_value cannot exceed max_value")


class ComparisonRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def compare(
        self,
        security_ids: list[UUID],
        *,
        metric_names: list[str] | None = None,
    ) -> dict[str, object]:
        ids = _unique_ids(security_ids)
        if not 2 <= len(ids) <= MAX_COMPARE_SECURITIES:
            raise ValueError("comparison requires between 2 and 5 distinct securities")
        metrics = _validate_metric_names(metric_names or sorted(PEER_USABLE_METRICS))
        async with self.engine.connect() as connection:
            securities = (
                await connection.execute(
                    text(
                        """
                        select id, legal_name, nse_symbol, bse_code, isin, currency, sector, industry
                        from securities
                        where id in :security_ids
                        order by nse_symbol nulls last, legal_name
                        """
                    ).bindparams(bindparam("security_ids", expanding=True)),
                    {"security_ids": ids},
                )
            ).mappings().all()
            metric_rows = (
                await connection.execute(
                    text(
                        """
                        select distinct on (sm.security_id, sm.metric_name)
                          sm.security_id, sm.metric_name, sm.as_of_date, sm.value, sm.unit,
                          sm.source_id, src.source_uri, src.title, src.source_type,
                          src.published_at, src.retrieved_at, src.freshness
                        from security_metrics sm
                        join sources src on src.id=sm.source_id
                        where sm.security_id in :security_ids
                          and sm.metric_name in :metric_names
                        order by sm.security_id, sm.metric_name, sm.as_of_date desc, sm.created_at desc
                        """
                    ).bindparams(
                        bindparam("security_ids", expanding=True),
                        bindparam("metric_names", expanding=True),
                    ),
                    {"security_ids": ids, "metric_names": metrics},
                )
            ).mappings().all()
            price_rows = (
                await connection.execute(
                    text(
                        """
                        select distinct on (mb.security_id)
                          mb.security_id, mb.ts, mb.close, mb.provider, mb.source_id,
                          src.source_uri, src.title
                        from market_bars mb
                        join sources src on src.id=mb.source_id
                        where mb.security_id in :security_ids
                          and mb.interval in ('1d','day','daily')
                        order by mb.security_id, mb.ts desc
                        """
                    ).bindparams(bindparam("security_ids", expanding=True)),
                    {"security_ids": ids},
                )
            ).mappings().all()

        security_map = {str(row["id"]): _row(row) for row in securities}
        if len(security_map) != len(ids):
            missing = [str(value) for value in ids if str(value) not in security_map]
            raise LookupError("security not found: " + ", ".join(missing))

        metrics_by_security: dict[str, dict[str, object]] = {key: {} for key in security_map}
        for row in metric_rows:
            security_key = str(row["security_id"])
            metrics_by_security[security_key][str(row["metric_name"])] = {
                "value": float(row["value"]),
                "unit": row.get("unit"),
                "as_of_date": _iso(row.get("as_of_date")),
                "source_id": str(row["source_id"]),
                "source_uri": row.get("source_uri"),
                "source_title": row.get("title"),
                "freshness": row.get("freshness"),
            }
        prices = {
            str(row["security_id"]): {
                "close": float(row["close"]),
                "as_of": _iso(row.get("ts")),
                "provider": row.get("provider"),
                "source_id": str(row["source_id"]),
                "source_uri": row.get("source_uri"),
                "source_title": row.get("title"),
            }
            for row in price_rows
        }

        companies: list[dict[str, object]] = []
        for security_id in ids:
            key = str(security_id)
            values = metrics_by_security[key]
            companies.append(
                {
                    **security_map[key],
                    "latest_price": prices.get(key),
                    "metrics": values,
                    "metric_coverage_count": len(values),
                    "metric_coverage_pct": round(len(values) / len(metrics) * 100.0, 2) if metrics else 0.0,
                }
            )

        rankings: dict[str, list[dict[str, object]]] = {}
        for metric in metrics:
            available: list[dict[str, object]] = []
            for company in companies:
                metric_value = _company_metric_value(company, metric)
                if metric_value is None:
                    continue
                available.append(
                    {
                        "security_id": str(company["id"]),
                        "nse_symbol": company.get("nse_symbol"),
                        "value": metric_value,
                    }
                )
            available.sort(key=_ranking_value, reverse=True)
            rankings[metric] = available

        return {
            "security_count": len(companies),
            "requested_metrics": metrics,
            "companies": companies,
            "rankings": rankings,
            "ranking_note": (
                "Rankings are raw metric orderings for comparison only. Higher or lower is not "
                "treated as universally better and no composite investment score is produced."
            ),
            "data_policy": "latest_source_linked_security_metrics_only",
        }

    async def screen(
        self,
        *,
        filters: list[MetricFilter],
        sector: str | None = None,
        industry: str | None = None,
        sort_metric: str | None = None,
        descending: bool = True,
        limit: int = 50,
    ) -> dict[str, object]:
        if not 1 <= len(filters) <= MAX_SCREEN_FILTERS:
            raise ValueError("screen requires between 1 and 5 metric filters")
        for item in filters:
            item.validate()
        sort_name = sort_metric or filters[0].metric_name
        _validate_metric_names([sort_name])
        result_limit = max(1, min(limit, MAX_SCREEN_RESULTS))
        requested_metrics = sorted({item.metric_name for item in filters} | {sort_name})

        conditions: list[str] = []
        params: dict[str, Any] = {
            "sector": sector.strip() if sector else None,
            "industry": industry.strip() if industry else None,
            "limit": result_limit,
            "sort_metric": sort_name,
        }
        for index, item in enumerate(filters):
            name_key = f"metric_{index}"
            params[name_key] = item.metric_name
            comparison_parts = [f"latest.metric_name = :{name_key}"]
            if item.min_value is not None:
                min_key = f"min_{index}"
                params[min_key] = item.min_value
                comparison_parts.append(f"latest.value >= :{min_key}")
            if item.max_value is not None:
                max_key = f"max_{index}"
                params[max_key] = item.max_value
                comparison_parts.append(f"latest.value <= :{max_key}")
            conditions.append(
                "exists (select 1 from latest where latest.security_id=s.id and "
                + " and ".join(comparison_parts)
                + ")"
            )

        where = " and ".join(conditions)
        order = "desc" if descending else "asc"
        query = text(
            f"""
            with latest as (
              select distinct on (sm.security_id, sm.metric_name)
                sm.security_id, sm.metric_name, sm.value, sm.unit, sm.as_of_date, sm.source_id
              from security_metrics sm
              where sm.source_id is not null
                and sm.metric_name in :requested_metrics
              order by sm.security_id, sm.metric_name, sm.as_of_date desc, sm.created_at desc
            )
            select s.id, s.legal_name, s.nse_symbol, s.bse_code, s.sector, s.industry,
                   sort_value.value as sort_value, sort_value.unit as sort_unit,
                   sort_value.as_of_date as sort_as_of_date, sort_value.source_id as sort_source_id
            from securities s
            join latest sort_value on sort_value.security_id=s.id and sort_value.metric_name=:sort_metric
            where s.primary_exchange='NSE'
              and coalesce(s.metadata->>'nse_series','EQ')='EQ'
              and (:sector is null or lower(coalesce(s.sector,'')) = lower(:sector))
              and (:industry is null or lower(coalesce(s.industry,'')) = lower(:industry))
              and {where}
            order by sort_value.value {order}, s.nse_symbol nulls last
            limit :limit
            """
        ).bindparams(bindparam("requested_metrics", expanding=True))
        params["requested_metrics"] = requested_metrics
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query, params)).mappings().all()
        return {
            "count": len(rows),
            "results": [
                {
                    **_row(row),
                    "sort_value": float(row["sort_value"]),
                    "sort_source_id": str(row["sort_source_id"]),
                }
                for row in rows
            ],
            "filters": [item.__dict__ for item in filters],
            "sort_metric": sort_name,
            "descending": descending,
            "sector": sector,
            "industry": industry,
            "data_policy": "source_linked_latest_metric_filtering_no_composite_score",
        }


def _company_metric_value(company: dict[str, object], metric: str) -> float | None:
    raw_metrics = company.get("metrics")
    if not isinstance(raw_metrics, dict):
        return None
    raw_metric = raw_metrics.get(metric)
    if not isinstance(raw_metric, dict):
        return None
    value = raw_metric.get("value")
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _ranking_value(item: dict[str, object]) -> float:
    value = item.get("value")
    if value is None:
        return float("-inf")
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return float("-inf")


def _validate_metric_names(metric_names: list[str]) -> list[str]:
    names = list(dict.fromkeys(metric_names))
    if not names:
        raise ValueError("at least one metric is required")
    for name in names:
        if not _METRIC_NAME_RE.fullmatch(name) or name not in PEER_USABLE_METRICS:
            raise ValueError(f"unsupported source-backed comparison metric: {name}")
    return names


def _unique_ids(values: list[UUID]) -> list[UUID]:
    return list(dict.fromkeys(values))


def _row(row: Any) -> dict[str, object]:
    return {key: _jsonable(value) for key, value in dict(row).items()}


def _jsonable(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
