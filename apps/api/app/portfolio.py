from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from math import sqrt
from statistics import pstdev
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

MARKET_FRESH_SECONDS = 7 * 24 * 60 * 60


class PortfolioRepository:
    """Private portfolio CRUD and source-backed deterministic analytics."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def list_for_user(self, user_id: UUID) -> list[dict[str, object]]:
        statement = text(
            """
            select p.id, p.name, p.base_currency, p.created_at, p.updated_at,
                   count(pp.security_id) as position_count
            from portfolios p
            left join portfolio_positions pp on pp.portfolio_id = p.id
            where p.user_id = :user_id
            group by p.id
            order by p.updated_at desc, p.name
            """
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement, {"user_id": user_id})).mappings().all()
        return [_row(row) for row in rows]

    async def create(self, user_id: UUID, name: str, base_currency: str = "INR") -> dict[str, object]:
        statement = text(
            """
            insert into portfolios (user_id, name, base_currency)
            values (:user_id, :name, :base_currency)
            returning id, name, base_currency, created_at, updated_at
            """
        )
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(
                    statement,
                    {
                        "user_id": user_id,
                        "name": name.strip(),
                        "base_currency": base_currency.strip().upper(),
                    },
                )
            ).mappings().one()
        return {**_row(row), "positions": []}

    async def delete(self, user_id: UUID, portfolio_id: UUID) -> bool:
        async with self.engine.begin() as connection:
            value = await connection.scalar(
                text("delete from portfolios where id=:id and user_id=:user_id returning id"),
                {"id": portfolio_id, "user_id": user_id},
            )
        return value is not None

    async def upsert_position(
        self,
        user_id: UUID,
        portfolio_id: UUID,
        security_id: UUID,
        *,
        quantity: float,
        average_cost: float | None,
        notes: str | None,
    ) -> dict[str, object] | None:
        statement = text(
            """
            insert into portfolio_positions (
                portfolio_id, security_id, quantity, average_cost, notes, updated_at
            )
            select :portfolio_id, :security_id, :quantity, :average_cost, :notes, now()
            where exists (
              select 1 from portfolios where id=:portfolio_id and user_id=:user_id
            )
            on conflict (portfolio_id, security_id) do update
            set quantity=excluded.quantity,
                average_cost=excluded.average_cost,
                notes=excluded.notes,
                updated_at=now()
            returning portfolio_id, security_id, quantity, average_cost, notes, added_at, updated_at
            """
        )
        params = {
            "portfolio_id": portfolio_id,
            "security_id": security_id,
            "user_id": user_id,
            "quantity": quantity,
            "average_cost": average_cost,
            "notes": notes.strip() if notes else None,
        }
        async with self.engine.begin() as connection:
            row = (await connection.execute(statement, params)).mappings().first()
            if row is not None:
                await connection.execute(
                    text("update portfolios set updated_at=now() where id=:id"),
                    {"id": portfolio_id},
                )
        return _row(row) if row is not None else None

    async def remove_position(
        self,
        user_id: UUID,
        portfolio_id: UUID,
        security_id: UUID,
    ) -> bool:
        statement = text(
            """
            delete from portfolio_positions pp
            using portfolios p
            where pp.portfolio_id=p.id
              and p.id=:portfolio_id
              and p.user_id=:user_id
              and pp.security_id=:security_id
            returning pp.security_id
            """
        )
        async with self.engine.begin() as connection:
            value = await connection.scalar(
                statement,
                {
                    "portfolio_id": portfolio_id,
                    "user_id": user_id,
                    "security_id": security_id,
                },
            )
            if value is not None:
                await connection.execute(
                    text("update portfolios set updated_at=now() where id=:id"),
                    {"id": portfolio_id},
                )
        return value is not None

    async def analyze(self, user_id: UUID, portfolio_id: UUID) -> dict[str, object] | None:
        async with self.engine.connect() as connection:
            portfolio = (
                await connection.execute(
                    text(
                        """
                        select id, name, base_currency, created_at, updated_at
                        from portfolios where id=:id and user_id=:user_id
                        """
                    ),
                    {"id": portfolio_id, "user_id": user_id},
                )
            ).mappings().one_or_none()
            if portfolio is None:
                return None
            positions = (
                await connection.execute(
                    text(
                        """
                        select pp.security_id, pp.quantity, pp.average_cost, pp.notes,
                               s.legal_name, s.nse_symbol, s.bse_code, s.currency, s.sector, s.industry,
                               latest.close as latest_close, latest.ts as latest_price_at,
                               latest.provider as latest_provider, latest.source_id as latest_source_id
                        from portfolio_positions pp
                        join securities s on s.id=pp.security_id
                        left join lateral (
                          select mb.close, mb.ts, mb.provider, mb.source_id
                          from market_bars mb
                          where mb.security_id=pp.security_id
                            and mb.source_id is not null
                            and mb.interval in ('1d','day','daily')
                          order by mb.ts desc
                          limit 1
                        ) latest on true
                        where pp.portfolio_id=:portfolio_id
                        order by s.nse_symbol nulls last, s.legal_name
                        """
                    ),
                    {"portfolio_id": portfolio_id},
                )
            ).mappings().all()
            history_rows = (
                await connection.execute(
                    text(
                        """
                        select pp.security_id, pp.quantity, mb.ts::date as bar_date, mb.close
                        from portfolio_positions pp
                        join market_bars mb on mb.security_id=pp.security_id
                        where pp.portfolio_id=:portfolio_id
                          and mb.source_id is not null
                          and mb.interval in ('1d','day','daily')
                          and mb.ts >= now() - interval '120 days'
                          and mb.close is not null
                        order by mb.ts
                        """
                    ),
                    {"portfolio_id": portfolio_id},
                )
            ).mappings().all()

        return _portfolio_analysis(
            dict(portfolio),
            [dict(row) for row in positions],
            [dict(row) for row in history_rows],
        )


def _portfolio_analysis(
    portfolio: dict[str, Any],
    positions: list[dict[str, Any]],
    history_rows: list[dict[str, Any]],
) -> dict[str, object]:
    now = datetime.now(UTC)
    current_rows: list[dict[str, object]] = []
    covered_value = 0.0
    total_known_cost_basis = 0.0
    matched_cost_basis = 0.0
    matched_market_value = 0.0
    positions_with_price = 0
    positions_with_known_pnl = 0
    stale_positions: list[str] = []
    missing_positions: list[str] = []

    for position in positions:
        quantity = _required_float(position["quantity"])
        average_cost = _float(position.get("average_cost"))
        latest_close = _float(position.get("latest_close"))
        latest_at = position.get("latest_price_at")
        symbol = str(position.get("nse_symbol") or position.get("legal_name"))
        is_fresh = bool(
            isinstance(latest_at, datetime)
            and (now - _utc(latest_at)).total_seconds() <= MARKET_FRESH_SECONDS
        )
        market_value = quantity * latest_close if latest_close is not None else None
        cost_basis = quantity * average_cost if average_cost is not None else None
        unrealized_pnl = (
            market_value - cost_basis
            if market_value is not None and cost_basis is not None
            else None
        )
        if market_value is not None:
            covered_value += market_value
            positions_with_price += 1
        else:
            missing_positions.append(symbol)
        if cost_basis is not None:
            total_known_cost_basis += cost_basis
        if unrealized_pnl is not None and market_value is not None and cost_basis is not None:
            matched_market_value += market_value
            matched_cost_basis += cost_basis
            positions_with_known_pnl += 1
        if latest_close is not None and not is_fresh:
            stale_positions.append(symbol)
        current_rows.append(
            {
                "security_id": str(position["security_id"]),
                "legal_name": position.get("legal_name"),
                "nse_symbol": position.get("nse_symbol"),
                "sector": position.get("sector"),
                "industry": position.get("industry"),
                "quantity": quantity,
                "average_cost": average_cost,
                "latest_close": latest_close,
                "latest_price_at": _iso(latest_at),
                "latest_provider": position.get("latest_provider"),
                "source_linked_price": position.get("latest_source_id") is not None,
                "price_fresh": is_fresh,
                "market_value": market_value,
                "cost_basis": cost_basis,
                "unrealized_pnl": unrealized_pnl,
                "notes": position.get("notes"),
            }
        )

    sector_values: dict[str, float] = defaultdict(float)
    industry_values: dict[str, float] = defaultdict(float)
    position_weights: list[float] = []
    for row in current_rows:
        value = _float(row.get("market_value"))
        if value is None:
            continue
        sector_values[str(row.get("sector") or "Unclassified")] += value
        industry_values[str(row.get("industry") or "Unclassified")] += value
        weight_pct = round(value / covered_value * 100.0, 4) if covered_value else None
        row["weight_pct"] = weight_pct
        if weight_pct is not None:
            position_weights.append(weight_pct / 100.0)

    sector_weights = _weights(sector_values, covered_value)
    industry_weights = _weights(industry_values, covered_value)
    hhi = sum(weight * weight for weight in position_weights)
    historical = _historical_portfolio_stats(positions, history_rows)

    risk_flags: list[str] = []
    if position_weights and max(position_weights) > 0.30:
        risk_flags.append("single_position_concentration_above_30pct")
    sector_weight_values = [_required_float(item["weight_pct"]) for item in sector_weights]
    if sector_weight_values and max(sector_weight_values) > 40.0:
        risk_flags.append("single_sector_concentration_above_40pct")
    if stale_positions:
        risk_flags.append("stale_source_linked_prices_present")
    if missing_positions:
        risk_flags.append("positions_missing_source_linked_prices")
    if positions and positions_with_price != len(positions):
        risk_flags.append("portfolio_valuation_is_partial")
    if positions and positions_with_known_pnl != len(positions):
        risk_flags.append("portfolio_pnl_is_partial")

    known_unrealized_pnl = (
        matched_market_value - matched_cost_basis if positions_with_known_pnl > 0 else None
    )
    return {
        "portfolio": {
            "id": str(portfolio["id"]),
            "name": portfolio["name"],
            "base_currency": portfolio["base_currency"],
            "updated_at": _iso(portfolio.get("updated_at")),
        },
        "position_count": len(positions),
        "positions_with_source_linked_price": positions_with_price,
        "positions_with_known_pnl": positions_with_known_pnl,
        "price_coverage_pct": round(positions_with_price / len(positions) * 100.0, 2) if positions else 0.0,
        "pnl_coverage_pct": round(positions_with_known_pnl / len(positions) * 100.0, 2) if positions else 0.0,
        "covered_market_value": round(covered_value, 4),
        "known_cost_basis": round(total_known_cost_basis, 4),
        "matched_cost_basis": round(matched_cost_basis, 4),
        "known_unrealized_pnl": round(known_unrealized_pnl, 4) if known_unrealized_pnl is not None else None,
        "positions": current_rows,
        "sector_weights": sector_weights,
        "industry_weights": industry_weights,
        "position_hhi": round(hhi, 6),
        "historical_risk": historical,
        "risk_flags": risk_flags,
        "limitations": {
            "valuation": "Only source-linked stored daily market prices are used.",
            "pnl": "Aggregate unrealized P&L includes only positions with both a source-linked price and known average cost.",
            "history": "Historical statistics require at least 30 common sourced daily observations across all positions.",
            "positions": "Quantities are treated as static research inputs; this is not broker execution or investment advice.",
        },
    }


def _historical_portfolio_stats(
    positions: list[dict[str, Any]],
    history_rows: list[dict[str, Any]],
) -> dict[str, object]:
    if not positions:
        return {"available": False, "reason": "portfolio_has_no_positions"}
    quantities = {
        str(row["security_id"]): _required_float(row["quantity"])
        for row in positions
    }
    by_date: dict[date, dict[str, float]] = defaultdict(dict)
    for row in history_rows:
        close = _float(row.get("close"))
        bar_date = row.get("bar_date")
        if close is None or not isinstance(bar_date, date):
            continue
        by_date[bar_date][str(row["security_id"])] = close
    common_values: list[tuple[date, float]] = []
    required = set(quantities)
    for bar_date, closes in by_date.items():
        if set(closes) >= required:
            common_values.append(
                (
                    bar_date,
                    sum(
                        quantities[security_id] * closes[security_id]
                        for security_id in required
                    ),
                )
            )
    common_values.sort(key=lambda item: item[0])
    if len(common_values) < 30:
        return {
            "available": False,
            "reason": "insufficient_common_source_linked_history",
            "common_observations": len(common_values),
        }
    values = [value for _, value in common_values[-60:]]
    returns = [
        values[index] / values[index - 1] - 1.0
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]
    if not returns:
        return {"available": False, "reason": "invalid_historical_values"}
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = min(max_drawdown, value / peak - 1.0)
    total_return = values[-1] / values[0] - 1.0 if values[0] > 0 else None
    volatility = pstdev(returns) * sqrt(252.0) if len(returns) >= 2 else None
    return {
        "available": True,
        "common_observations": len(values),
        "first_date": str(common_values[-len(values)][0]),
        "last_date": str(common_values[-1][0]),
        "total_return_pct": round(total_return * 100.0, 4) if total_return is not None else None,
        "annualized_volatility_pct": round(volatility * 100.0, 4) if volatility is not None else None,
        "max_drawdown_pct": round(max_drawdown * 100.0, 4),
    }


def _weights(values: dict[str, float], total: float) -> list[dict[str, object]]:
    if total <= 0:
        return []
    return [
        {
            "name": key,
            "market_value": round(value, 4),
            "weight_pct": round(value / total * 100.0, 4),
        }
        for key, value in sorted(values.items(), key=lambda item: item[1], reverse=True)
    ]


def _row(row: Any) -> dict[str, object]:
    return {key: _jsonable(value) for key, value in dict(row).items()}


def _jsonable(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return value


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _required_float(value: object) -> float:
    result = _float(value)
    if result is None:
        raise ValueError(f"Expected numeric value, received {value!r}")
    return result


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
