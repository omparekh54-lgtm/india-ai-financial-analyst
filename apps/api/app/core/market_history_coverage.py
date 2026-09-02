from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.market_history_policy import MarketHistoryPolicyResult, evaluate_market_history


@dataclass(frozen=True)
class SecurityMarketHistoryCoverage:
    security_id: str
    symbol: str
    result: MarketHistoryPolicyResult


@dataclass(frozen=True)
class MarketHistoryCoverageReport:
    total_securities: int
    complete_securities: int
    technical_computable_securities: int
    history_limited_recent_listings: int
    securities: tuple[SecurityMarketHistoryCoverage, ...]

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
            if item.result.complete and not item.result.technical_agent_computable
        )

    def as_dict(self, *, preview_limit: int = 50) -> dict[str, object]:
        return {
            "total_securities": self.total_securities,
            "complete_securities": self.complete_securities,
            "complete_coverage_pct": _coverage_pct(
                self.complete_securities,
                self.total_securities,
            ),
            "technical_computable_securities": self.technical_computable_securities,
            "history_limited_recent_listings": self.history_limited_recent_listings,
            "complete": self.complete,
            "incomplete_symbols_preview": list(self.incomplete_symbols[:preview_limit]),
            "history_limited_symbols_preview": list(
                self.history_limited_symbols[:preview_limit]
            ),
        }


def parse_listing_date(value: object) -> date | None:
    if value is None:
        return None
    cleaned = str(value).strip().upper()
    if not cleaned or cleaned in {"-", "--", "NA", "N/A"}:
        return None
    for pattern in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(cleaned, pattern).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


async def load_market_history_coverage(
    engine: AsyncEngine,
    *,
    as_of: date | None = None,
) -> MarketHistoryCoverageReport:
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
                      count(distinct mb.ts::date) filter (
                        where mb.source_id is not null
                          and mb.interval in ('1d', 'day', 'daily')
                      ) as sourced_daily_bars,
                      min(mb.ts::date) filter (
                        where mb.source_id is not null
                          and mb.interval in ('1d', 'day', 'daily')
                      ) as first_sourced_bar_date,
                      max(mb.ts::date) filter (
                        where mb.source_id is not null
                          and mb.interval in ('1d', 'day', 'daily')
                      ) as last_sourced_bar_date
                    from securities s
                    left join market_bars mb on mb.security_id = s.id
                    where s.primary_exchange = 'NSE'
                      and coalesce(s.metadata->>'nse_series', 'EQ') = 'EQ'
                    group by s.id, s.nse_symbol, s.metadata->>'date_of_listing'
                    order by s.nse_symbol
                    """
                )
            )
        ).mappings().all()

    securities: list[SecurityMarketHistoryCoverage] = []
    for row in rows:
        result = evaluate_market_history(
            listing_date=parse_listing_date(row.get("date_of_listing")),
            as_of=evaluation_date,
            bar_count=_int(row.get("sourced_daily_bars")),
            first_bar_date=_date(row.get("first_sourced_bar_date")),
            last_bar_date=_date(row.get("last_sourced_bar_date")),
        )
        securities.append(
            SecurityMarketHistoryCoverage(
                security_id=str(row["id"]),
                symbol=str(row.get("nse_symbol") or row["id"]),
                result=result,
            )
        )

    complete = sum(item.result.complete for item in securities)
    computable = sum(
        item.result.complete and item.result.technical_agent_computable for item in securities
    )
    history_limited = sum(
        item.result.complete and not item.result.technical_agent_computable for item in securities
    )
    return MarketHistoryCoverageReport(
        total_securities=len(securities),
        complete_securities=complete,
        technical_computable_securities=computable,
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
