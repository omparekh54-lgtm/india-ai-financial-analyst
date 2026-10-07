"""Load the source-linked inputs used to derive peer metrics for one security."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ingestion.derived_metrics import MetricFinancialFact, MetricMarketClose


async def load_metric_facts(engine: AsyncEngine, security_id: UUID) -> list[MetricFinancialFact]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    with ranked as (
                      select ff.fact_name, ff.period_start, ff.period_end, ff.period_type,
                             ff.value, ff.unit, ff.data,
                             ff.source_id,
                             row_number() over (
                               partition by ff.fact_name, ff.period_type
                               order by ff.period_end desc, ff.created_at desc
                             ) as rn
                      from financial_facts ff
                      where ff.security_id = :security_id
                        and ff.source_id is not null
                    )
                    select fact_name, period_start, period_end, period_type,
                           value, unit, source_id, data
                    from ranked
                    where rn <= 8
                    order by fact_name, period_end desc
                    """
                ),
                {"security_id": security_id},
            )
        ).mappings().all()
    return [
        MetricFinancialFact(
            fact_name=str(row["fact_name"]),
            period_end=_date(row["period_end"]),
            period_type=str(row["period_type"]),
            value=Decimal(str(row["value"])),
            unit=str(row["unit"]) if row["unit"] is not None else None,
            source_id=UUID(str(row["source_id"])),
            period_start=_date(row["period_start"]) if row["period_start"] else None,
            data=dict(row["data"] or {}),
        )
        for row in rows
    ]


async def load_metric_market_close(engine: AsyncEngine, security_id: UUID) -> MetricMarketClose | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    select mb.ts, mb.close, mb.source_id
                    from market_bars mb join sources src on src.id = mb.source_id
                    where mb.security_id = :security_id
                      and interval in ('1d', 'day', 'daily')
                      and source_id is not null
                      and close is not null
                    order by ts desc,
                             (coalesce(src.metadata->>'production_approved', 'false')='true') desc
                    limit 1
                    """
                ),
                {"security_id": security_id},
            )
        ).mappings().first()
    if row is None:
        return None
    ts = row["ts"]
    as_of_date = ts.date() if isinstance(ts, datetime) else _date(ts)
    return MetricMarketClose(
        as_of_date=as_of_date,
        price=Decimal(str(row["close"])),
        source_id=UUID(str(row["source_id"])),
    )


def _date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
