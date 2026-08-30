from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BenchmarkBootstrapSpec:
    code: str
    provider: str
    file: Path


@dataclass(frozen=True)
class FinancialBootstrapSpec:
    security: str
    file: Path
    source_uri: str


@dataclass(frozen=True)
class BootstrapStage:
    name: str
    command: tuple[str, ...]


def parse_benchmark_spec(value: str) -> BenchmarkBootstrapSpec:
    parts = [part.strip() for part in value.split(",", 2)]
    if len(parts) != 3 or not all(parts):
        raise ValueError("benchmark must use CODE,PROVIDER,FILE format")
    code, provider, file_name = parts
    return BenchmarkBootstrapSpec(
        code=code.upper(),
        provider=provider.lower(),
        file=Path(file_name),
    )


def parse_financial_spec(value: str) -> FinancialBootstrapSpec:
    parts = [part.strip() for part in value.split(",", 2)]
    if len(parts) != 3 or not all(parts):
        raise ValueError("financial must use SECURITY,FILE,SOURCE_URI format")
    security, file_name, source_uri = parts
    return FinancialBootstrapSpec(
        security=security,
        file=Path(file_name),
        source_uri=source_uri,
    )


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
    benchmarks: tuple[BenchmarkBootstrapSpec, ...],
    benchmark_interval: str,
    benchmark_timezone: str,
    benchmark_min_rows: int,
    macro_files: tuple[Path, ...],
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
    if benchmark_min_rows < 1:
        raise ValueError("benchmark_min_rows must be >= 1")
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

    for index, spec in enumerate(benchmarks, start=1):
        command = [
            python_executable,
            str(scripts_dir / "import_reference_csv.py"),
            "benchmark",
            "--file",
            str(spec.file),
            "--benchmark-code",
            spec.code,
            "--provider",
            spec.provider,
            "--interval",
            benchmark_interval,
            "--timezone",
            benchmark_timezone,
            "--min-rows",
            str(benchmark_min_rows),
        ]
        if dry_run:
            command.append("--dry-run")
        stages.append(BootstrapStage(name=f"benchmark_{index}_{spec.code}", command=tuple(command)))

    for index, path in enumerate(macro_files, start=1):
        command = [
            python_executable,
            str(scripts_dir / "import_reference_csv.py"),
            "macro",
            "--file",
            str(path),
            "--min-rows",
            str(macro_min_rows),
        ]
        if dry_run:
            command.append("--dry-run")
        stages.append(BootstrapStage(name=f"macro_{index}", command=tuple(command)))

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
