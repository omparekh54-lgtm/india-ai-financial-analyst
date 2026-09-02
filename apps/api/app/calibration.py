from __future__ import annotations

import json
from dataclasses import dataclass
from statistics import mean, median
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

SUPPORTED_HORIZONS = (20, 60, 120)
DEFAULT_BENCHMARK_CODE = "NIFTY50"


@dataclass(frozen=True)
class EvaluationOutcome:
    horizon_sessions: int
    start_bar_date: object
    end_bar_date: object
    start_price: float
    end_price: float
    stock_return_pct: float
    benchmark_code: str | None
    benchmark_start_price: float | None
    benchmark_end_price: float | None
    benchmark_return_pct: float | None
    excess_return_pct: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "horizon_sessions": self.horizon_sessions,
            "start_bar_date": str(self.start_bar_date),
            "end_bar_date": str(self.end_bar_date),
            "start_price": self.start_price,
            "end_price": self.end_price,
            "stock_return_pct": self.stock_return_pct,
            "benchmark_code": self.benchmark_code,
            "benchmark_start_price": self.benchmark_start_price,
            "benchmark_end_price": self.benchmark_end_price,
            "benchmark_return_pct": self.benchmark_return_pct,
            "excess_return_pct": self.excess_return_pct,
        }


class CalibrationRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def evaluate_due_snapshots(
        self,
        *,
        limit: int = 100,
        benchmark_code: str = DEFAULT_BENCHMARK_CODE,
    ) -> dict[str, object]:
        max_limit = max(1, min(limit, 500))
        async with self.engine.begin() as connection:
            snapshots = (
                await connection.execute(
                    text(
                        """
                        select snap.id, snap.security_id, snap.snapshot_at, snap.thesis_hash,
                               snap.metrics, snap.job_id
                        from analysis_snapshots snap
                        join research_jobs job on job.id=snap.job_id
                        where job.status='completed'
                          and exists (
                            select 1 from market_bars mb
                            where mb.security_id=snap.security_id
                              and mb.source_id is not null
                              and mb.interval in ('1d','day','daily')
                              and mb.ts::date > snap.snapshot_at::date
                          )
                        order by snap.snapshot_at
                        limit :limit
                        """
                    ),
                    {"limit": max_limit},
                )
            ).mappings().all()

            created = 0
            skipped = 0
            for snapshot in snapshots:
                for horizon in SUPPORTED_HORIZONS:
                    exists = await connection.scalar(
                        text(
                            "select 1 from research_evaluations where snapshot_id=:id and horizon_sessions=:horizon"
                        ),
                        {"id": snapshot["id"], "horizon": horizon},
                    )
                    if exists:
                        continue
                    bars = (
                        await connection.execute(
                            text(
                                """
                                select mb.ts::date as bar_date, mb.close
                                from market_bars mb
                                where mb.security_id=:security_id
                                  and mb.source_id is not null
                                  and mb.interval in ('1d','day','daily')
                                  and mb.ts::date > :snapshot_date
                                  and mb.close is not null
                                order by mb.ts::date, mb.ts
                                limit :needed
                                """
                            ),
                            {
                                "security_id": snapshot["security_id"],
                                "snapshot_date": snapshot["snapshot_at"].date(),
                                "needed": horizon + 1,
                            },
                        )
                    ).mappings().all()
                    if len(bars) < horizon + 1:
                        skipped += 1
                        continue
                    start = bars[0]
                    end = bars[horizon]
                    outcome = await _outcome_with_benchmark(
                        connection,
                        start_date=start["bar_date"],
                        end_date=end["bar_date"],
                        start_price=float(start["close"]),
                        end_price=float(end["close"]),
                        horizon=horizon,
                        benchmark_code=benchmark_code,
                    )
                    metrics = snapshot["metrics"] if isinstance(snapshot["metrics"], dict) else {}
                    raw_confidence = metrics.get("confidence")
                    confidence: dict[str, Any] = (
                        raw_confidence if isinstance(raw_confidence, dict) else {}
                    )
                    result = await connection.execute(
                        text(
                            """
                            insert into research_evaluations (
                              snapshot_id, security_id, horizon_sessions,
                              start_bar_date, end_bar_date, start_price, end_price, stock_return_pct,
                              benchmark_code, benchmark_start_price, benchmark_end_price,
                              benchmark_return_pct, excess_return_pct, thesis_hash, confidence, metadata
                            ) values (
                              :snapshot_id, :security_id, :horizon_sessions,
                              :start_bar_date, :end_bar_date, :start_price, :end_price, :stock_return_pct,
                              :benchmark_code, :benchmark_start_price, :benchmark_end_price,
                              :benchmark_return_pct, :excess_return_pct, :thesis_hash,
                              cast(:confidence as jsonb), cast(:metadata as jsonb)
                            )
                            on conflict (snapshot_id, horizon_sessions) do nothing
                            returning id
                            """
                        ),
                        {
                            "snapshot_id": snapshot["id"],
                            "security_id": snapshot["security_id"],
                            **outcome.as_dict(),
                            "thesis_hash": snapshot["thesis_hash"],
                            "confidence": json.dumps(confidence, default=str),
                            "metadata": json.dumps(
                                {
                                    "calculation_version": 1,
                                    "start_rule": "first_source_linked_daily_bar_strictly_after_snapshot_date",
                                    "end_rule": f"{horizon}th_subsequent_source_linked_trading_session",
                                    "benchmark_date_rule": "same_exact_start_and_end_dates_only",
                                    "no_lookahead": True,
                                }
                            ),
                        },
                    )
                    if result.scalar_one_or_none() is not None:
                        created += 1
        return {
            "snapshots_considered": len(snapshots),
            "evaluations_created": created,
            "insufficient_future_history": skipped,
            "supported_horizons": list(SUPPORTED_HORIZONS),
            "benchmark_code": benchmark_code,
            "data_policy": "source_linked_future_bars_only_no_lookahead",
        }

    async def summary_for_user(self, user_id: UUID) -> dict[str, object]:
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        select e.horizon_sessions, e.stock_return_pct, e.excess_return_pct,
                               e.confidence, e.evaluated_at
                        from research_evaluations e
                        join analysis_snapshots snap on snap.id=e.snapshot_id
                        join research_jobs job on job.id=snap.job_id
                        where job.requested_by=:user_id
                        order by e.evaluated_at desc
                        """
                    ),
                    {"user_id": user_id},
                )
            ).mappings().all()
        return summarize_evaluations([dict(row) for row in rows])


def summarize_evaluations(rows: list[dict[str, Any]]) -> dict[str, object]:
    by_horizon: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_horizon.setdefault(int(row["horizon_sessions"]), []).append(row)
    horizons: dict[str, object] = {}
    for horizon, items in sorted(by_horizon.items()):
        stock_returns = [float(item["stock_return_pct"]) for item in items]
        excess_returns = [
            float(item["excess_return_pct"])
            for item in items
            if item.get("excess_return_pct") is not None
        ]
        horizons[str(horizon)] = {
            "sample_count": len(items),
            "benchmark_matched_count": len(excess_returns),
            "mean_stock_return_pct": round(mean(stock_returns), 4),
            "median_stock_return_pct": round(median(stock_returns), 4),
            "mean_excess_return_pct": round(mean(excess_returns), 4) if excess_returns else None,
            "median_excess_return_pct": round(median(excess_returns), 4) if excess_returns else None,
            "positive_excess_rate_pct": (
                round(sum(value > 0 for value in excess_returns) / len(excess_returns) * 100.0, 2)
                if excess_returns
                else None
            ),
            "confidence_buckets": _confidence_buckets(items),
        }
    return {
        "evaluation_count": len(rows),
        "horizons": horizons,
        "interpretation": (
            "These are realized post-research outcomes, not proof of forecasting skill. "
            "Confidence buckets are descriptive calibration diagnostics and are never used to "
            "rewrite historical research or create a trading signal."
        ),
    }


async def _outcome_with_benchmark(
    connection: Any,
    *,
    start_date: object,
    end_date: object,
    start_price: float,
    end_price: float,
    horizon: int,
    benchmark_code: str,
) -> EvaluationOutcome:
    stock_return = (end_price / start_price - 1.0) * 100.0
    benchmark_rows = (
        await connection.execute(
            text(
                """
                select bb.ts::date as bar_date, bb.close
                from benchmark_bars bb
                join benchmarks b on b.id=bb.benchmark_id
                where b.code=:code
                  and bb.source_id is not null
                  and bb.ts::date in (:start_date, :end_date)
                  and bb.close is not null
                order by bb.ts::date
                """
            ),
            {"code": benchmark_code, "start_date": start_date, "end_date": end_date},
        )
    ).mappings().all()
    benchmark_by_date = {str(row["bar_date"]): float(row["close"]) for row in benchmark_rows}
    b_start = benchmark_by_date.get(str(start_date))
    b_end = benchmark_by_date.get(str(end_date))
    benchmark_return = (
        (b_end / b_start - 1.0) * 100.0
        if b_start is not None and b_end is not None and b_start > 0
        else None
    )
    excess = stock_return - benchmark_return if benchmark_return is not None else None
    return EvaluationOutcome(
        horizon_sessions=horizon,
        start_bar_date=start_date,
        end_bar_date=end_date,
        start_price=start_price,
        end_price=end_price,
        stock_return_pct=round(stock_return, 6),
        benchmark_code=benchmark_code if benchmark_return is not None else None,
        benchmark_start_price=b_start,
        benchmark_end_price=b_end,
        benchmark_return_pct=round(benchmark_return, 6) if benchmark_return is not None else None,
        excess_return_pct=round(excess, 6) if excess is not None else None,
    )


def _confidence_buckets(items: list[dict[str, Any]]) -> dict[str, object]:
    buckets: dict[str, list[float]] = {"low": [], "medium": [], "high": []}
    for item in items:
        raw_confidence = item.get("confidence")
        confidence: dict[str, Any] = raw_confidence if isinstance(raw_confidence, dict) else {}
        value = confidence.get("thesis_confidence")
        if not isinstance(value, (int, float)):
            continue
        bucket = "low" if value < 0.45 else "medium" if value < 0.75 else "high"
        excess_return = item.get("excess_return_pct")
        if excess_return is not None:
            buckets[bucket].append(float(excess_return))
    return {
        key: {
            "sample_count": len(values),
            "mean_excess_return_pct": round(mean(values), 4) if values else None,
        }
        for key, values in buckets.items()
    }
