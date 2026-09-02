from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.financial_history_policy import (
    FinancialHistoryPolicyResult,
    evaluate_financial_history,
)
from app.core.market_history_coverage import parse_listing_date


@dataclass(frozen=True)
class SecurityFinancialHistoryCoverage:
    security_id: str
    symbol: str
    result: FinancialHistoryPolicyResult


@dataclass(frozen=True)
class FinancialHistoryCoverageReport:
    total_securities: int
    complete_securities: int
    history_limited_recent_listings: int
    securities: tuple[SecurityFinancialHistoryCoverage, ...]

    @property
    def complete(self) -> bool:
        return self.total_securities > 0 and self.complete_securities == self.total_securities

    @property
    def incomplete_symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.securities if not item.result.complete)

    @property
    def history_limited_symbols(self) -> tuple[str, ...]:
        return tuple(
            item.symbol
            for item in self.securities
            if item.result.history_limited_recent_listing
        )

    def as_dict(self, *, preview_limit: int = 50) -> dict[str, object]:
        return {
            "total_securities": self.total_securities,
            "complete_securities": self.complete_securities,
            "complete_coverage_pct": _coverage_pct(
                self.complete_securities,
                self.total_securities,
            ),
            "history_limited_recent_listings": self.history_limited_recent_listings,
            "complete": self.complete,
            "incomplete_symbols_preview": list(self.incomplete_symbols[:preview_limit]),
            "history_limited_symbols_preview": list(
                self.history_limited_symbols[:preview_limit]
            ),
        }


async def load_financial_history_coverage(
    engine: AsyncEngine,
    *,
    as_of: date | None = None,
) -> FinancialHistoryCoverageReport:
    evaluation_date = as_of or datetime.now(UTC).date()
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    select
                      s.id,
                      s.nse_symbol,
                      s.metadata->>'date_of_listing' as date_of_listing,
                      count(distinct ff.period_end) filter (
                        where ff.source_id is not null
                      ) as sourced_periods,
                      count(distinct ff.fact_name) filter (
                        where ff.source_id is not null
                      ) as sourced_fact_types,
                      max(ff.period_end) filter (
                        where ff.source_id is not null
                      ) as latest_sourced_period_end
                    from securities s
                    left join financial_facts ff on ff.security_id = s.id
                    where s.primary_exchange = 'NSE'
                      and coalesce(s.metadata->>'nse_series', 'EQ') = 'EQ'
                    group by s.id, s.nse_symbol, s.metadata->>'date_of_listing'
                    order by s.nse_symbol
                    """
                )
            )
        ).mappings().all()

    securities: list[SecurityFinancialHistoryCoverage] = []
    for row in rows:
        result = evaluate_financial_history(
            listing_date=parse_listing_date(row.get("date_of_listing")),
            as_of=evaluation_date,
            period_count=_int(row.get("sourced_periods")),
            fact_type_count=_int(row.get("sourced_fact_types")),
            latest_period_end=_date(row.get("latest_sourced_period_end")),
        )
        securities.append(
            SecurityFinancialHistoryCoverage(
                security_id=str(row["id"]),
                symbol=str(row.get("nse_symbol") or row["id"]),
                result=result,
            )
        )

    complete = sum(item.result.complete for item in securities)
    history_limited = sum(
        item.result.history_limited_recent_listing for item in securities
    )
    return FinancialHistoryCoverageReport(
        total_securities=len(securities),
        complete_securities=complete,
        history_limited_recent_listings=history_limited,
        securities=tuple(securities),
    )


def _int(value: Any) -> int:
    return int(value or 0)


def _date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _coverage_pct(covered: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((covered / total) * 100.0, 2)
