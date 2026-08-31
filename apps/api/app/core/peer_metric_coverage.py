from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

INDUSTRY_COMPARABLE_METRICS = frozenset(
    {"revenue_growth", "ebitda_margin", "roce", "pe", "pb", "ev_ebitda"}
)
MIN_COMPARABLE_METRICS = 3
PEER_METRIC_MAX_AGE_DAYS = 400


@dataclass(frozen=True)
class PeerMetricCoverageReport:
    total_securities: int
    complete_securities: int
    incomplete_symbols: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.total_securities > 0 and self.complete_securities == self.total_securities

    def as_dict(self, *, preview_limit: int = 50) -> dict[str, object]:
        return {
            "total_securities": self.total_securities,
            "complete_securities": self.complete_securities,
            "complete_coverage_pct": _coverage_pct(
                self.complete_securities, self.total_securities
            ),
            "minimum_comparable_metrics": MIN_COMPARABLE_METRICS,
            "allowed_metric_names": sorted(INDUSTRY_COMPARABLE_METRICS),
            "complete": self.complete,
            "incomplete_symbols_preview": list(self.incomplete_symbols[:preview_limit]),
        }


async def load_peer_metric_coverage(engine: AsyncEngine) -> PeerMetricCoverageReport:
    """Require recent, source-backed metrics that the Industry Agent can actually compare."""
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    with nse_eq as (
                      select id, nse_symbol
                      from securities
                      where primary_exchange = 'NSE'
                        and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
                    ), counts as (
                      select
                        sm.security_id,
                        count(distinct sm.metric_name) as metric_count
                      from security_metrics sm
                      join nse_eq n on n.id = sm.security_id
                      join sources src on src.id = sm.source_id
                      where sm.as_of_date >= current_date - :max_age_days
                        and sm.metric_name = any(:metric_names)
                        and nullif(btrim(coalesce(src.checksum, '')), '') is not null
                        and (
                          (
                            src.source_type = 'derived_security_metric'
                            and src.metadata->>'provenance_class'
                              = 'derived_from_source_linked_inputs'
                            and coalesce(src.metadata->>'production_approved', 'false') = 'true'
                            and jsonb_array_length(
                              coalesce(src.metadata->'upstream_source_ids', '[]'::jsonb)
                            ) > 0
                          )
                          or (
                            src.source_type <> 'derived_security_metric'
                            and coalesce(src.metadata->>'production_approved', 'false') = 'true'
                            and src.metadata->>'provenance_class'
                              in ('official_source', 'licensed_or_approved')
                          )
                        )
                      group by sm.security_id
                    )
                    select n.id, n.nse_symbol, coalesce(c.metric_count, 0) as metric_count
                    from nse_eq n
                    left join counts c on c.security_id = n.id
                    order by n.nse_symbol
                    """
                ),
                {
                    "max_age_days": PEER_METRIC_MAX_AGE_DAYS,
                    "metric_names": sorted(INDUSTRY_COMPARABLE_METRICS),
                },
            )
        ).mappings().all()

    incomplete = tuple(
        str(row["nse_symbol"] or row["id"])
        for row in rows
        if int(row["metric_count"] or 0) < MIN_COMPARABLE_METRICS
    )
    return PeerMetricCoverageReport(
        total_securities=len(rows),
        complete_securities=len(rows) - len(incomplete),
        incomplete_symbols=incomplete,
    )


def _coverage_pct(covered: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((covered / total) * 100.0, 2)
