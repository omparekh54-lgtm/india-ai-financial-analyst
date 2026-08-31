from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.ingestion.official_benchmark_files import resolve_official_benchmark_source
from app.ingestion.official_macro_files import validate_official_source_url, validate_rbi_series_key
from app.ingestion.reference_provenance import (
    validate_provider_name,
    validate_reference_approval,
    validate_source_uri,
)


@dataclass(frozen=True)
class BenchmarkBootstrapSpec:
    code: str
    file: Path
    source_url: str


@dataclass(frozen=True)
class FinancialBootstrapSpec:
    security: str
    file: Path
    source_uri: str
    approval_reference: str | None = None


@dataclass(frozen=True)
class MarketBootstrapSpec:
    security: str
    provider: str
    file: Path
    source_uri: str
    approval_reference: str | None = None


@dataclass(frozen=True)
class MetricsBootstrapSpec:
    security: str
    file: Path
    source_uri: str
    approval_reference: str | None = None


@dataclass(frozen=True)
class MacroBootstrapSpec:
    provider: str
    file: Path
    source_url: str
    series_key: str | None = None


@dataclass(frozen=True)
class BootstrapStage:
    name: str
    command: tuple[str, ...]


def parse_benchmark_spec(value: str) -> BenchmarkBootstrapSpec:
    parts = [part.strip() for part in value.split(",", 2)]
    if len(parts) != 3 or not all(parts):
        raise ValueError("benchmark must use CODE,FILE,OFFICIAL_SOURCE_URL format")
    code, file_name, source_url = parts
    source = resolve_official_benchmark_source(code, source_url)
    return BenchmarkBootstrapSpec(
        code=source.benchmark_code,
        file=Path(file_name),
        source_url=source.source_url,
    )


def parse_financial_spec(value: str) -> FinancialBootstrapSpec:
    parts = [part.strip() for part in value.split(",", 3)]
    if len(parts) not in {3, 4} or not all(parts[:3]):
        raise ValueError(
            "financial must use SECURITY,FILE,SOURCE_URI[,APPROVAL_REFERENCE] format"
        )
    security, file_name, source_uri = parts[:3]
    approval_reference = parts[3] if len(parts) == 4 and parts[3] else None
    cleaned_uri = validate_source_uri(source_uri)
    approval = validate_reference_approval(cleaned_uri, approval_reference)
    return FinancialBootstrapSpec(
        security=security,
        file=Path(file_name),
        source_uri=cleaned_uri,
        approval_reference=approval.approval_reference,
    )


def parse_market_spec(value: str) -> MarketBootstrapSpec:
    parts = [part.strip() for part in value.split(",", 4)]
    if len(parts) not in {4, 5} or not all(parts[:4]):
        raise ValueError(
            "market must use SECURITY,PROVIDER,FILE,SOURCE_URI[,APPROVAL_REFERENCE] format"
        )
    security, provider, file_name, source_uri = parts[:4]
    approval_reference = parts[4] if len(parts) == 5 and parts[4] else None
    cleaned_uri = validate_source_uri(source_uri)
    approval = validate_reference_approval(cleaned_uri, approval_reference)
    return MarketBootstrapSpec(
        security=security,
        provider=validate_provider_name(provider),
        file=Path(file_name),
        source_uri=cleaned_uri,
        approval_reference=approval.approval_reference,
    )


def parse_metrics_spec(value: str) -> MetricsBootstrapSpec:
    parts = [part.strip() for part in value.split(",", 3)]
    if len(parts) not in {3, 4} or not all(parts[:3]):
        raise ValueError(
            "metrics must use SECURITY,FILE,SOURCE_URI[,APPROVAL_REFERENCE] format"
        )
    security, file_name, source_uri = parts[:3]
    approval_reference = parts[3] if len(parts) == 4 and parts[3] else None
    cleaned_uri = validate_source_uri(source_uri)
    approval = validate_reference_approval(cleaned_uri, approval_reference)
    return MetricsBootstrapSpec(
        security=security,
        file=Path(file_name),
        source_uri=cleaned_uri,
        approval_reference=approval.approval_reference,
    )


def parse_macro_spec(value: str) -> MacroBootstrapSpec:
    provider_text, separator, remainder = value.partition(",")
    provider = provider_text.strip().lower()
    if not separator or not provider:
        raise ValueError(
            "macro must use RBI,SERIES_KEY,FILE,OFFICIAL_SOURCE_URL or "
            "NSDL,FILE,OFFICIAL_SOURCE_URL format"
        )

    if provider == "rbi":
        parts = [part.strip() for part in remainder.split(",", 2)]
        if len(parts) != 3 or not all(parts):
            raise ValueError("RBI macro must use RBI,SERIES_KEY,FILE,OFFICIAL_SOURCE_URL format")
        series_key, file_name, source_url = parts
        return MacroBootstrapSpec(
            provider="rbi",
            series_key=validate_rbi_series_key(series_key),
            file=Path(file_name),
            source_url=validate_official_source_url("RBI", source_url),
        )
    if provider == "nsdl":
        parts = [part.strip() for part in remainder.split(",", 1)]
        if len(parts) != 2 or not all(parts):
            raise ValueError("NSDL macro must use NSDL,FILE,OFFICIAL_SOURCE_URL format")
        file_name, source_url = parts
        return MacroBootstrapSpec(
            provider="nsdl",
            file=Path(file_name),
            source_url=validate_official_source_url("NSDL", source_url),
        )
    raise ValueError("macro provider must be RBI or NSDL")


def build_bootstrap_plan(
    *,
    python_executable: str,
    scripts_dir: Path,
    skip_nse: bool,
    security_master_provider: str,
    nse_file: Path | None,
    nse_url: str | None,
    upstox_file: Path | None,
    upstox_url: str | None,
    upstox_approval_reference: str | None,
    nse_min_rows: int,
    run_nse_benchmarks: bool,
    nse_benchmark_min_rows: int,
    financials: tuple[FinancialBootstrapSpec, ...],
    financial_min_rows: int,
    markets: tuple[MarketBootstrapSpec, ...],
    market_interval: str,
    market_timezone: str,
    market_min_rows: int,
    metrics: tuple[MetricsBootstrapSpec, ...],
    metrics_min_rows: int,
    benchmarks: tuple[BenchmarkBootstrapSpec, ...],
    benchmark_interval: str,
    benchmark_timezone: str,
    benchmark_min_rows: int,
    macros: tuple[MacroBootstrapSpec, ...],
    macro_min_rows: int,
    dry_run: bool,
    run_official_feeds: bool,
    official_feed_limit: int,
    embed_evidence: bool,
    embedding_limit: int | None,
) -> tuple[BootstrapStage, ...]:
    master_provider = security_master_provider.strip().lower()
    if master_provider not in {"nse", "upstox"}:
        raise ValueError("security_master_provider must be nse or upstox")
    if nse_min_rows < 1000:
        raise ValueError("nse_min_rows must be >= 1000 for the production NSE universe")
    if nse_benchmark_min_rows < 2:
        raise ValueError("nse_benchmark_min_rows must be >= 2")
    if financial_min_rows < 1:
        raise ValueError("financial_min_rows must be >= 1")
    if market_min_rows < 1:
        raise ValueError("market_min_rows must be >= 1")
    if metrics_min_rows < 1:
        raise ValueError("metrics_min_rows must be >= 1")
    if benchmark_min_rows < 2:
        raise ValueError("benchmark_min_rows must be >= 2")
    if benchmark_interval.strip().lower() != "1d":
        raise ValueError("official benchmark bootstrap currently supports interval=1d only")
    if macro_min_rows < 1:
        raise ValueError("macro_min_rows must be >= 1")
    if official_feed_limit < 1 or official_feed_limit > 20:
        raise ValueError("official_feed_limit must be between 1 and 20")
    if embedding_limit is not None and embedding_limit < 1:
        raise ValueError("embedding_limit must be >= 1")
    if dry_run and (run_official_feeds or embed_evidence):
        raise ValueError(
            "dry-run cannot execute official feeds or embedding writes; disable those stages"
        )

    stages: list[BootstrapStage] = []
    if not skip_nse:
        command = [
            python_executable,
            str(scripts_dir / "bootstrap_nse_universe.py"),
            "--provider",
            master_provider,
            "--min-rows",
            str(nse_min_rows),
        ]
        if master_provider == "nse":
            if upstox_file is not None or upstox_url:
                raise ValueError("Upstox security-master options require provider=upstox")
            if nse_file is not None:
                command.extend(["--nse-file", str(nse_file)])
            elif nse_url:
                command.extend(["--nse-url", nse_url])
        else:
            if nse_file is not None or nse_url:
                raise ValueError("NSE security-master options require provider=nse")
            if upstox_file is not None:
                command.extend(["--upstox-file", str(upstox_file)])
            if upstox_url:
                command.extend(["--upstox-url", upstox_url])
            if upstox_approval_reference:
                command.extend(
                    ["--upstox-approval-reference", upstox_approval_reference]
                )
        if dry_run:
            command.append("--dry-run")
        stages.append(BootstrapStage(name="nse_universe", command=tuple(command)))

    if run_nse_benchmarks:
        command = [
            python_executable,
            str(scripts_dir / "backfill_nse_benchmarks.py"),
            "--min-rows",
            str(nse_benchmark_min_rows),
        ]
        if dry_run:
            command.append("--dry-run")
        stages.append(BootstrapStage(name="nse_benchmarks", command=tuple(command)))

    for index, financial_spec in enumerate(financials, start=1):
        command = [
            python_executable,
            str(scripts_dir / "import_financial_csv.py"),
            "--file",
            str(financial_spec.file),
            "--security",
            financial_spec.security,
            "--source-uri",
            financial_spec.source_uri,
            "--min-rows",
            str(financial_min_rows),
        ]
        _append_approval_reference(command, financial_spec.approval_reference)
        if dry_run:
            command.append("--dry-run")
        stages.append(
            BootstrapStage(
                name=f"financial_{index}_{financial_spec.security.upper()}",
                command=tuple(command),
            )
        )

    for index, market_spec in enumerate(markets, start=1):
        command = [
            python_executable,
            str(scripts_dir / "import_market_csv.py"),
            "--file",
            str(market_spec.file),
            "--security",
            market_spec.security,
            "--source-uri",
            market_spec.source_uri,
            "--provider",
            market_spec.provider,
            "--interval",
            market_interval,
            "--timezone",
            market_timezone,
            "--min-rows",
            str(market_min_rows),
        ]
        _append_approval_reference(command, market_spec.approval_reference)
        if dry_run:
            command.append("--dry-run")
        stages.append(
            BootstrapStage(
                name=f"market_{index}_{market_spec.security.upper()}",
                command=tuple(command),
            )
        )

    for index, metrics_spec in enumerate(metrics, start=1):
        command = [
            python_executable,
            str(scripts_dir / "import_security_metrics_csv.py"),
            "--file",
            str(metrics_spec.file),
            "--security",
            metrics_spec.security,
            "--source-uri",
            metrics_spec.source_uri,
            "--min-rows",
            str(metrics_min_rows),
        ]
        _append_approval_reference(command, metrics_spec.approval_reference)
        if dry_run:
            command.append("--dry-run")
        stages.append(
            BootstrapStage(
                name=f"metrics_{index}_{metrics_spec.security.upper()}",
                command=tuple(command),
            )
        )

    for index, benchmark_spec in enumerate(benchmarks, start=1):
        command = [
            python_executable,
            str(scripts_dir / "import_official_benchmark_file.py"),
            "--file",
            str(benchmark_spec.file),
            "--source-url",
            benchmark_spec.source_url,
            "--benchmark-code",
            benchmark_spec.code,
            "--timezone",
            benchmark_timezone,
            "--min-rows",
            str(benchmark_min_rows),
        ]
        if dry_run:
            command.append("--dry-run")
        stages.append(
            BootstrapStage(
                name=f"benchmark_{index}_{benchmark_spec.code}",
                command=tuple(command),
            )
        )

    for index, macro_spec in enumerate(macros, start=1):
        command = [
            python_executable,
            str(scripts_dir / "import_official_macro_file.py"),
            macro_spec.provider,
            "--file",
            str(macro_spec.file),
            "--source-url",
            macro_spec.source_url,
            "--min-rows",
            str(macro_min_rows),
        ]
        if macro_spec.provider == "rbi":
            if macro_spec.series_key is None:
                raise ValueError("RBI macro bootstrap requires a series_key")
            command.extend(["--series-key", macro_spec.series_key])
        if dry_run:
            command.append("--dry-run")
        stage_suffix = macro_spec.series_key or "flows"
        stages.append(
            BootstrapStage(
                name=f"macro_{index}_{macro_spec.provider.upper()}_{stage_suffix}",
                command=tuple(command),
            )
        )

    if run_official_feeds:
        stages.append(
            BootstrapStage(
                name="official_feeds",
                command=(
                    python_executable,
                    str(scripts_dir / "run_official_feed_worker.py"),
                    "--limit",
                    str(official_feed_limit),
                ),
            )
        )

    if embed_evidence:
        command = [python_executable, str(scripts_dir / "backfill_evidence_embeddings.py")]
        if embedding_limit is not None:
            command.extend(["--limit", str(embedding_limit)])
        stages.append(BootstrapStage(name="evidence_embeddings", command=tuple(command)))

    return tuple(stages)


def _append_approval_reference(command: list[str], approval_reference: str | None) -> None:
    if approval_reference:
        command.extend(["--approval-reference", approval_reference])
