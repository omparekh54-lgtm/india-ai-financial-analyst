from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class DataCoverage:
    nse_eq_securities: int
    provider_instruments: int
    financial_facts: int
    sourced_financial_facts: int
    corporate_events: int
    sources: int
    evidence_chunks: int
    embedded_evidence_chunks: int
    market_bars: int
    sourced_market_bars: int
    benchmark_bars: int
    sourced_benchmark_bars: int
    macro_observations: int
    sourced_macro_observations: int
    security_metrics: int
    sourced_security_metrics: int
    enabled_official_feeds: int
    latest_financial_period: date | None = None
    latest_corporate_event: datetime | None = None
    latest_market_bar: datetime | None = None
    latest_benchmark_bar: datetime | None = None
    latest_macro_observation: date | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "nse_eq_securities": self.nse_eq_securities,
            "provider_instruments": self.provider_instruments,
            "financial_facts": self.financial_facts,
            "sourced_financial_facts": self.sourced_financial_facts,
            "corporate_events": self.corporate_events,
            "sources": self.sources,
            "evidence_chunks": self.evidence_chunks,
            "embedded_evidence_chunks": self.embedded_evidence_chunks,
            "market_bars": self.market_bars,
            "sourced_market_bars": self.sourced_market_bars,
            "benchmark_bars": self.benchmark_bars,
            "sourced_benchmark_bars": self.sourced_benchmark_bars,
            "macro_observations": self.macro_observations,
            "sourced_macro_observations": self.sourced_macro_observations,
            "security_metrics": self.security_metrics,
            "sourced_security_metrics": self.sourced_security_metrics,
            "enabled_official_feeds": self.enabled_official_feeds,
            "latest_financial_period": _iso(self.latest_financial_period),
            "latest_corporate_event": _iso(self.latest_corporate_event),
            "latest_market_bar": _iso(self.latest_market_bar),
            "latest_benchmark_bar": _iso(self.latest_benchmark_bar),
            "latest_macro_observation": _iso(self.latest_macro_observation),
        }


@dataclass(frozen=True)
class DataCoverageReport:
    coverage: DataCoverage
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "coverage": self.coverage.as_dict(),
        }


async def load_data_coverage(engine: AsyncEngine) -> DataCoverage:
    statement = text(
        """
        select
          (select count(*) from securities
             where primary_exchange = 'NSE'
               and coalesce(metadata->>'nse_series', 'EQ') = 'EQ') as nse_eq_securities,
          (select count(*) from provider_instruments) as provider_instruments,
          (select count(*) from financial_facts) as financial_facts,
          (select count(*) from financial_facts where source_id is not null)
            as sourced_financial_facts,
          (select count(*) from corporate_events) as corporate_events,
          (select count(*) from sources) as sources,
          (select count(*) from evidence_chunks) as evidence_chunks,
          (select count(*) from evidence_chunks where embedding is not null) as embedded_evidence_chunks,
          (select count(*) from market_bars) as market_bars,
          (select count(*) from market_bars where source_id is not null) as sourced_market_bars,
          (select count(*) from benchmark_bars) as benchmark_bars,
          (select count(*) from benchmark_bars where source_id is not null) as sourced_benchmark_bars,
          (select count(*) from macro_observations) as macro_observations,
          (select count(*) from macro_observations where source_id is not null)
            as sourced_macro_observations,
          (select count(*) from security_metrics) as security_metrics,
          (select count(*) from security_metrics where source_id is not null)
            as sourced_security_metrics,
          (select count(*) from official_data_feeds where enabled) as enabled_official_feeds,
          (select max(period_end) from financial_facts) as latest_financial_period,
          (select max(event_at) from corporate_events) as latest_corporate_event,
          (select max(ts) from market_bars) as latest_market_bar,
          (select max(ts) from benchmark_bars) as latest_benchmark_bar,
          (select max(observation_date) from macro_observations) as latest_macro_observation
        """
    )
    async with engine.connect() as connection:
        row = (await connection.execute(statement)).mappings().one()
    return DataCoverage(
        nse_eq_securities=int(row["nse_eq_securities"] or 0),
        provider_instruments=int(row["provider_instruments"] or 0),
        financial_facts=int(row["financial_facts"] or 0),
        sourced_financial_facts=int(row["sourced_financial_facts"] or 0),
        corporate_events=int(row["corporate_events"] or 0),
        sources=int(row["sources"] or 0),
        evidence_chunks=int(row["evidence_chunks"] or 0),
        embedded_evidence_chunks=int(row["embedded_evidence_chunks"] or 0),
        market_bars=int(row["market_bars"] or 0),
        sourced_market_bars=int(row["sourced_market_bars"] or 0),
        benchmark_bars=int(row["benchmark_bars"] or 0),
        sourced_benchmark_bars=int(row["sourced_benchmark_bars"] or 0),
        macro_observations=int(row["macro_observations"] or 0),
        sourced_macro_observations=int(row["sourced_macro_observations"] or 0),
        security_metrics=int(row["security_metrics"] or 0),
        sourced_security_metrics=int(row["sourced_security_metrics"] or 0),
        enabled_official_feeds=int(row["enabled_official_feeds"] or 0),
        latest_financial_period=row["latest_financial_period"],
        latest_corporate_event=row["latest_corporate_event"],
        latest_market_bar=row["latest_market_bar"],
        latest_benchmark_bar=row["latest_benchmark_bar"],
        latest_macro_observation=row["latest_macro_observation"],
    )


def evaluate_data_coverage(
    coverage: DataCoverage,
    *,
    min_nse_eq_securities: int = 1000,
    as_of: datetime | None = None,
    market_max_age_days: int = 7,
    benchmark_max_age_days: int = 7,
    macro_max_age_days: int = 45,
    corporate_event_max_age_days: int = 90,
    financial_period_max_age_days: int = 200,
) -> DataCoverageReport:
    errors: list[str] = []
    warnings: list[str] = []
    now = _utc(as_of or datetime.now(UTC))

    if coverage.nse_eq_securities < min_nse_eq_securities:
        errors.append(
            "NSE EQ security master is below the production threshold: "
            f"{coverage.nse_eq_securities} < {min_nse_eq_securities}."
        )
    if coverage.provider_instruments < coverage.nse_eq_securities:
        warnings.append(
            "Provider instrument coverage is lower than the NSE security universe; "
            "some symbols may not resolve to market-data adapters."
        )
    if coverage.financial_facts == 0:
        warnings.append("No normalized financial facts are populated.")
    else:
        _require_full_provenance(
            errors,
            label="normalized financial facts",
            sourced=coverage.sourced_financial_facts,
            total=coverage.financial_facts,
        )
        if _date_age_days(now, coverage.latest_financial_period) > financial_period_max_age_days:
            warnings.append(
                "Normalized financial facts appear stale; latest period is "
                f"{_iso(coverage.latest_financial_period)}."
            )
    if coverage.corporate_events == 0 or coverage.evidence_chunks == 0:
        warnings.append("No parsed corporate-event filing evidence is populated.")
    elif _datetime_age_days(now, coverage.latest_corporate_event) > corporate_event_max_age_days:
        warnings.append(
            "Corporate-event filing evidence appears stale; latest event is "
            f"{_iso(coverage.latest_corporate_event)}."
        )
    if coverage.market_bars == 0:
        warnings.append("No stored security market bars are populated.")
    else:
        _require_full_provenance(
            errors,
            label="stored security market bars",
            sourced=coverage.sourced_market_bars,
            total=coverage.market_bars,
        )
        if _datetime_age_days(now, coverage.latest_market_bar) > market_max_age_days:
            warnings.append(
                "Stored security market bars appear stale; latest bar is "
                f"{_iso(coverage.latest_market_bar)}."
            )
    if coverage.benchmark_bars == 0:
        warnings.append("No NIFTY/India VIX benchmark bars are populated.")
    else:
        _require_full_provenance(
            errors,
            label="NIFTY/India VIX benchmark bars",
            sourced=coverage.sourced_benchmark_bars,
            total=coverage.benchmark_bars,
        )
        if _datetime_age_days(now, coverage.latest_benchmark_bar) > benchmark_max_age_days:
            warnings.append(
                "NIFTY/India VIX benchmark bars appear stale; latest bar is "
                f"{_iso(coverage.latest_benchmark_bar)}."
            )
    if coverage.macro_observations == 0:
        warnings.append("No India/global macro observations are populated.")
    else:
        _require_full_provenance(
            errors,
            label="India/global macro observations",
            sourced=coverage.sourced_macro_observations,
            total=coverage.macro_observations,
        )
        if _date_age_days(now, coverage.latest_macro_observation) > macro_max_age_days:
            warnings.append(
                "India/global macro observations appear stale; latest observation is "
                f"{_iso(coverage.latest_macro_observation)}."
            )
    if coverage.security_metrics == 0:
        warnings.append("No comparable/security metrics are populated for peer analysis.")
    else:
        _require_full_provenance(
            errors,
            label="comparable/security metrics",
            sourced=coverage.sourced_security_metrics,
            total=coverage.security_metrics,
        )
    if coverage.sources > 0 and coverage.evidence_chunks > 0 and coverage.embedded_evidence_chunks == 0:
        warnings.append("Evidence exists but semantic embedding backfill has not populated vectors.")
    if coverage.enabled_official_feeds == 0:
        warnings.append(
            "No official automated data feeds are enabled; this is expected until the approved "
            "NSE/BSE production-data strategy is activated."
        )

    return DataCoverageReport(
        coverage=coverage,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _require_full_provenance(
    errors: list[str],
    *,
    label: str,
    sourced: int,
    total: int,
) -> None:
    if sourced < total:
        errors.append(
            f"{label} contain rows without source provenance: {sourced}/{total} are source-linked."
        )


def _datetime_age_days(now: datetime, value: datetime | None) -> int:
    if value is None:
        return 0
    age = now - _utc(value)
    return max(age.days, 0)


def _date_age_days(now: datetime, value: date | None) -> int:
    if value is None:
        return 0
    return max((now.date() - value).days, 0)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
