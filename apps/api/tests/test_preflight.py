from __future__ import annotations

from app.core.preflight import DatabasePreflight


def _preflight(**overrides: object) -> DatabasePreflight:
    values: dict[str, object] = {
        "connected": True,
        "vector_extension": True,
        "semantic_index": True,
        "research_ownership_column": True,
        "benchmark_source_column": True,
        "reference_source_approval_constraint": True,
        "missing_tables": (),
        "rls_disabled_tables": (),
        "missing_owner_policies": (),
        "error_type": None,
    }
    values.update(overrides)
    return DatabasePreflight(**values)  # type: ignore[arg-type]


def test_complete_database_contract_is_ready() -> None:
    assert _preflight().ready is True


def test_missing_owner_policy_fails_preflight() -> None:
    report = _preflight(missing_owner_policies=("claims_owner_read",))
    assert report.ready is False
    assert report.as_dict()["missing_owner_policies"] == ["claims_owner_read"]


def test_missing_benchmark_source_column_fails_preflight() -> None:
    report = _preflight(benchmark_source_column=False)
    assert report.ready is False
    assert report.as_dict()["benchmark_source_column"] is False


def test_missing_reference_approval_constraint_fails_preflight() -> None:
    report = _preflight(reference_source_approval_constraint=False)
    assert report.ready is False
    assert report.as_dict()["reference_source_approval_constraint"] is False


def test_disabled_rls_fails_preflight() -> None:
    assert _preflight(rls_disabled_tables=("analysis_snapshots",)).ready is False


def test_database_error_fails_preflight() -> None:
    assert _preflight(connected=False, error_type="OperationalError").ready is False
