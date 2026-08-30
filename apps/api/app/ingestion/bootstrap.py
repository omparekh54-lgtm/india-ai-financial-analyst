from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.ingestion.official_benchmark_files import resolve_official_benchmark_source
from app.ingestion.official_macro_files import validate_official_source_url, validate_rbi_series_key
from app.ingestion.reference_provenance import validate_provider_name, validate_source_uri


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


@dataclass(frozen=True)
class MarketBootstrapSpec:
    security: str
    provider: str
    file: Path
    source_uri: str


@dataclass(frozen=True)
class MetricsBootstrapSpec:
    security: str
    file: Path
    source_uri: str


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
    parts = [part.strip() for part in value.split(",", 2)]
    if len(parts) != 3 or not all(parts):
        raise ValueError("financial must use SECURITY,FILE,SOURCE_URI format")
    security, file_name, source_uri = parts
    return FinancialBootstrapSpec(
        security=security,
        file=Path(file_name),
        source_uri=validate_source_uri(source_uri),
    )


def parse_market_spec(value: str) -> MarketBootstrapSpec:
    parts = [part.strip() for part in value.split(",", 3)]
    if len(parts) != 4 or not all(parts):
        raise ValueError("market must use SECURITY,PROVIDER,FILE,SOURCE_URI format")
    security, provider, file_name, source_uri = parts
    return MarketBootstrapSpec(
        security=security,
        provider=validate_provider_name(provider),
        file=Path(file_name),
        source_uri=validate_source_uri(source_uri),
    )


def parse_metrics_spec(value: str) -> MetricsBootstrapSpec:
    parts = [part.strip() for part in value.split(",", 2)]
    if len(parts) != 3 or not all(parts):
        raise ValueError("metrics must use SECURITY,FILE,SOURCE_URI format")
    security, file_name, source_uri = parts
    return MetricsBootstrapSpec(
        security=security,
        file=Path(file_name),
        source_uri=validate_source_uri(source_uri),
    )


def parse_macro_spec(value: str) -> MacroBootstrapSpec:
    raw_parts = [part.strip() for part in value.split(",")]
    if not raw_parts or not raw_parts[0]:
        raise ValueError(
            "macro must use RBI,SERIES_KEY,FILE,OFFICIAL_SOURCE_URL or "
            "NSDL,FILE,OFFICIAL_SOURCE_URL format"
        )

    provider = raw_parts[0].lower()
    if provider == "rbi":
        if len(raw_parts) != 4 or not all(raw_parts):
            raise ValueError("RBI macro must use RBI,SERIES_KEY,FILE,OFFICIAL_SOURCE_URL format")
        _, series_key, file_name, source_url = raw_parts
        return MacroBootstrapSpec(
            provider="rbi",
            series_key=validate_rbi_series_key(series_key),
            file=Path(file_name),
            source_url=validate_official_source_url("RBI", source_url),
        )
    if provider == "nsdl":
        if len(raw_parts) != 3 or not all(raw_parts):
            raise ValueError("NSDL macro must use NSDL,FILE,OFFICIAL_SOURCE_URL format")
        _, file_name, source_url = raw_parts
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
    nse_file: Path | None,
    nse_url: str | None,
    nse_min_rows: int,
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
    if nse_min_rows < 1:
        raise ValueError("nse_min_rows must be >= 1")
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
            str(scripts_dir / "import_nse_security_master.py"),
            "--min-rows",
            str(nse_min_rows),
        ]
        if nse_file is not None:
            command.extend(["--file", str(nse_file)])
        elif nse_url:
            command.extend(["--url", nse_url])
        if dry_run:
            command.append("--dry-run")
        stages.append(BootstrapStage(name="nse_security_master", command=tuple(command)))

    for index, spec in enumerate(financials, start=1):
        command = [
            python_executable,
            str(scripts_dir / "import_financial_csv.py"),
            "--file",
            str(spec.file),
            "--security",
            spec.security,
            "--source-uri",
            spec.source_uri,
            "--min-rows",
            str(financial_min_rows),
        ]
        if dry_run:
            command.append("--dry-run")
        stages.append(
            BootstrapStage(
                name=f"financial_{index}_{spec.security.upper()}",
                command=tuple(command),
            )
        )

    for index, spec in enumerate(markets, start=1):
        command = [
            python_executable,
            str(scripts_dir / "import_market_csv.py"),
            "--file",
            str(spec.file),
            "--security",
            spec.security,
            "--source-uri",
            spec.source_uri,
            "--provider",
            spec.provider,
            "--interval",
            market_interval,
            "--timezone",
            market_timezone,
            "--min-rows",
            str(market_min_rows),
        ]
        if dry_run:
            command.append("--dry-run")
        stages.append(
            BootstrapStage(
                name=f"market_{index}_{spec.security.upper()}",
                command=tuple(command),
            )
        )

    for index, spec in enumerate(metrics, start=1):
        command = [
            python_executable,
            str(scripts_dir / "import_security_metrics_csv.py"),
            "--file",
            str(spec.file),
            "--security",
            spec.security,
            "--source-uri",
            spec.source_uri,
            "--min-rows",
            str(metrics_min_rows),
        ]
        if dry_run:
            command.append("--dry-run")
        stages.append(
            BootstrapStage(
                name=f"metrics_{index}_{spec.security.upper()}",
                command=tuple(command),
            )
        )

    for index, spec in enumerate(benchmarks, start=1):
        command = [
            python_executable,
            str(scripts_dir / "import_official_benchmark_file.py"),
            "--file",
            str(spec.file),
            "--source-url",
            spec.source_url,
            "--benchmark-code",
            spec.code,
            "--timezone",
            benchmark_timezone,
            "--min-rows",
            str(benchmark_min_rows),
        ]
        if dry_run:
            command.append("--dry-run")
        stages.append(BootstrapStage(name=f"benchmark_{index}_{spec.code}", command=tuple(command)))

    for index, spec in enumerate(macros, start=1):
        command = [
            python_executable,
            str(scripts_dir / "import_official_macro_file.py"),
            spec.provider,
            "--file",
            str(spec.file),
            "--source-url",
            spec.source_url,
            "--min-rows",
            str(macro_min_rows),
        ]
        if spec.provider == "rbi":
            if spec.series_key is None:
                raise ValueError("RBI macro bootstrap requires a series_key")
            command.extend(["--series-key", spec.series_key])
        if dry_run:
            command.append("--dry-run")
        stage_suffix = spec.series_key or "flows"
        stages.append(
            BootstrapStage(
                name=f"macro_{index}_{spec.provider.upper()}_{stage_suffix}",
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
